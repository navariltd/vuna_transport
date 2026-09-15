"""Explainable availability for standard ERPNext Drivers."""

from collections import Counter, defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, get_datetime, getdate, now_datetime

from vuna_transport.services.fleet.vehicle_availability_service import (
	ACTIVE_ALLOCATION_STATUSES,
	ACTIVE_TRIP_STATUSES,
)
from vuna_transport.services.shared.resource_scheduling_service import interval_overlaps


def get_driver_status(driver, at_time=None):
	at_time = get_datetime(at_time or now_datetime())
	results = get_driver_availability_map(
		driver_names=[driver],
		from_time=at_time,
		to_time=add_to_date(at_time, seconds=1),
	)
	return results.get(driver) or _missing_driver_result(driver)


def evaluate_driver_availability(
	driver,
	from_time,
	to_time,
	*,
	exclude_allocation=None,
	exclude_delivery_load=None,
	expected_company=None,
):
	results = get_driver_availability_map(
		driver_names=[driver],
		from_time=from_time,
		to_time=to_time,
		exclude_allocation=exclude_allocation,
		exclude_delivery_load=exclude_delivery_load,
		expected_company=expected_company,
	)
	return results.get(driver) or _missing_driver_result(driver)


def get_driver_availability_summary(from_time, to_time, *, driver_names=None):
	results = get_driver_availability_map(
		driver_names=driver_names,
		from_time=from_time,
		to_time=to_time,
	)
	counts = Counter(result.operational_state for result in results.values())
	return {
		"total": len(results),
		"active": sum(1 for result in results.values() if result.driver_status == "Active"),
		"available": counts["Available"],
		"allocated": counts["Allocated"],
		"in_trip": counts["In Trip"],
		"unavailable": counts["Unavailable"],
		"drivers": list(results.values()),
	}


def get_driver_availability_map(
	*,
	driver_names=None,
	from_time=None,
	to_time=None,
	exclude_allocation=None,
	exclude_delivery_load=None,
	company=None,
	expected_company=None,
):
	from_time, to_time = _normalize_interval(from_time, to_time)
	filters = {}
	if driver_names:
		filters["name"] = ["in", list(dict.fromkeys(driver_names))]
	drivers = frappe.get_all(
		"Driver",
		filters=filters,
		fields=["name", "full_name", "status", "expiry_date", "employee"],
		order_by="full_name asc, name asc",
	)
	if not drivers:
		return {}
	employee_names = {driver.employee for driver in drivers if driver.employee}
	employee_companies = (
		dict(
			frappe.get_all(
				"Employee",
				filters={"name": ["in", list(employee_names)]},
				fields=["name", "company"],
				as_list=True,
			)
		)
		if employee_names
		else {}
	)
	for driver in drivers:
		driver.company = employee_companies.get(driver.employee)
	if company:
		drivers = [driver for driver in drivers if not driver.company or driver.company == company]
	if not drivers:
		return {}

	driver_names = [driver.name for driver in drivers]
	allocations = frappe.get_all(
		"Vehicle Allocation",
		filters={
			"driver": ["in", driver_names],
			"docstatus": 1,
			"status": ["in", ACTIVE_ALLOCATION_STATUSES],
		},
		fields=[
			"name",
			"driver",
			"effective_from",
			"effective_to",
		],
	)
	allocation_load_rows = (
		frappe.get_all(
			"Vehicle Allocation Load",
			filters={"parent": ["in", [row.name for row in allocations]], "parenttype": "Vehicle Allocation"},
			fields=["parent", "delivery_load"],
		)
		if allocations
		else []
	)
	allocation_load_names = defaultdict(list)
	for row in allocation_load_rows:
		allocation_load_names[row.parent].append(row.delivery_load)
	for allocation in allocations:
		allocation.delivery_loads = allocation_load_names.get(allocation.name, [])
	trips = frappe.get_all(
		"Delivery Trip",
		filters={"docstatus": ["!=", 2], "status": ["in", ACTIVE_TRIP_STATUSES]},
		or_filters=[
			{"driver": ["in", driver_names]},
			{"vuna_current_driver": ["in", driver_names]},
		],
		fields=[
			"name",
			"driver",
			"vuna_current_driver",
			"vuna_delivery_load",
			"departure_time",
			"status",
		],
	)
	trips = [trip for trip in trips if (trip.vuna_current_driver or trip.driver) in driver_names]
	load_names = {
		load_name for row in allocations for load_name in row.delivery_loads if load_name
	} | {
		row.vuna_delivery_load for row in trips if row.vuna_delivery_load
	}
	loads = (
		{
			row.name: row
			for row in frappe.get_all(
				"Delivery Load",
				filters={"name": ["in", list(load_names)]},
				fields=["name", "planned_departure", "planned_arrival"],
			)
		}
		if load_names
		else {}
	)

	allocations_by_driver = defaultdict(list)
	for allocation in allocations:
		if allocation.name == exclude_allocation:
			continue
		if exclude_delivery_load and exclude_delivery_load in allocation.delivery_loads:
			continue
		allocations_by_driver[allocation.driver].append(allocation)
	trips_by_driver = defaultdict(list)
	for trip in trips:
		if exclude_delivery_load and trip.vuna_delivery_load == exclude_delivery_load:
			continue
		trips_by_driver[trip.vuna_current_driver or trip.driver].append(trip)

	return {
		driver.name: _evaluate_driver(
			driver,
			allocations_by_driver.get(driver.name, []),
			trips_by_driver.get(driver.name, []),
			loads,
			from_time,
			to_time,
			expected_company,
		)
		for driver in drivers
	}


def _evaluate_driver(driver, allocations, trips, loads, from_time, to_time, expected_company=None):
	reasons = []
	state = "Available"
	current_allocation = None
	current_trip = None
	next_available_at = None
	if driver.status != "Active":
		reasons.append(
			_reason(
				"DRIVER_INACTIVE",
				_("Driver status is {0}.").format(driver.status or _("not set")),
			)
		)
	if expected_company and getattr(driver, "company", None) and driver.company != expected_company:
		reasons.append(
			_reason(
				"COMPANY_MISMATCH",
				_("Driver belongs to Company {0}.").format(driver.company),
			)
		)
	if driver.expiry_date and getdate(driver.expiry_date) < getdate(to_time):
		reasons.append(
			_reason(
				"LICENCE_EXPIRED",
				_("Driving licence expires before the planning window ends ({0}).").format(
					driver.expiry_date
				),
			)
		)

	for trip in trips:
		load = loads.get(trip.vuna_delivery_load)
		trip_from, trip_to = _trip_window(trip, load)
		if trip.status != "In Transit" and not interval_overlaps(from_time, to_time, trip_from, trip_to):
			continue
		state = "In Trip"
		current_trip = current_trip or trip.name
		reasons.append(
			_reason(
				"ACTIVE_TRIP",
				_("Driver is assigned to active Delivery Trip {0}.").format(trip.name),
				"Delivery Trip",
				trip.name,
			)
		)
		if trip.status != "In Transit":
			next_available_at = _latest_datetime(next_available_at, trip_to)

	for allocation in allocations:
		allocation_load_names = getattr(allocation, "delivery_loads", None) or []
		allocation_loads = [loads.get(name) for name in allocation_load_names if loads.get(name)]
		allocation_from = min(
			(get_datetime(load.planned_departure) for load in allocation_loads if load.planned_departure),
			default=allocation.effective_from,
		)
		allocation_to = max(
			(get_datetime(load.planned_arrival) for load in allocation_loads if load.planned_arrival),
			default=allocation.effective_to,
		)
		if not interval_overlaps(from_time, to_time, allocation_from, allocation_to):
			continue
		if state != "In Trip":
			state = "Allocated"
		current_allocation = current_allocation or allocation.name
		reasons.append(
			_reason(
				"ACTIVE_ALLOCATION",
				_("Driver is allocated to Delivery Load(s) {0}.").format(", ".join(allocation_load_names)),
				"Vehicle Allocation",
				allocation.name,
			)
		)
		next_available_at = _latest_datetime(next_available_at, allocation_to)

	if reasons and state == "Available":
		state = "Unavailable"
	return frappe._dict(
		driver=driver.name,
		driver_name=driver.full_name or driver.name,
		driver_status=driver.status,
		company=getattr(driver, "company", None),
		operational_state=state,
		dispatch_eligible=not reasons,
		primary_reason=_primary_reason(reasons, state),
		reasons=reasons,
		current_allocation=current_allocation,
		current_trip=current_trip,
		next_available_at=next_available_at,
	)


def _primary_reason(reasons, state):
	if not reasons:
		return None
	preferred_code = {
		"In Trip": "ACTIVE_TRIP",
		"Allocated": "ACTIVE_ALLOCATION",
	}.get(state)
	if preferred_code:
		match = next((reason for reason in reasons if reason.code == preferred_code), None)
		if match:
			return match.message
	return reasons[0].message


def _trip_window(trip, load):
	if load and load.planned_departure and load.planned_arrival:
		return load.planned_departure, load.planned_arrival
	departure = trip.departure_time
	return departure, add_days(departure, 1) if departure else None


def _reason(code, message, reference_doctype=None, reference_name=None):
	return frappe._dict(
		code=code,
		message=message,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)


def _normalize_interval(from_time, to_time):
	if str(from_time).lower() in ("undefined", "null") or str(to_time).lower() in (
		"undefined",
		"null",
	):
		frappe.throw(_("Select a valid planning date and time window."))
	try:
		from_time = get_datetime(from_time or now_datetime())
		to_time = get_datetime(to_time or add_to_date(from_time, seconds=1))
	except (TypeError, ValueError):
		frappe.throw(_("Select a valid planning date and time window."))
	# A current-availability request may lose sub-second precision in transit.
	# Treat a zero/near-zero interval as a point-in-time check, but still reject
	# genuinely reversed windows.
	if from_time <= to_time and (to_time - from_time).total_seconds() <= 1:
		to_time = add_to_date(from_time, seconds=1)
	if from_time >= to_time:
		frappe.throw(_("Availability end time must be after start time."))
	return from_time, to_time


def _latest_datetime(current, candidate):
	if not candidate:
		return current
	candidate = get_datetime(candidate)
	return candidate if not current or candidate > get_datetime(current) else current


def _missing_driver_result(driver):
	reason = _reason("DRIVER_NOT_FOUND", _("Driver {0} does not exist.").format(driver))
	return frappe._dict(
		driver=driver,
		driver_name=driver,
		driver_status=None,
		company=None,
		operational_state="Unavailable",
		dispatch_eligible=False,
		primary_reason=reason.message,
		reasons=[reason],
		current_allocation=None,
		current_trip=None,
		next_available_at=None,
	)
