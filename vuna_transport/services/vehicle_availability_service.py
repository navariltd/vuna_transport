"""Canonical, explainable physical Vehicle availability."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, get_datetime, now_datetime

from vuna_transport.services.resource_scheduling_service import interval_overlaps


ACTIVE_ALLOCATION_STATUSES = ("Allocated", "Accepted")
ACTIVE_TRIP_STATUSES = ("Scheduled", "In Transit")
VEHICLE_STATES = (
	"Available",
	"Allocated",
	"Loading",
	"Ready for Dispatch",
	"In Trip",
	"Unavailable",
)


def get_vehicle_status(vehicle, at_time=None):
	"""Return the current operational state of one Vehicle."""
	at_time = get_datetime(at_time or now_datetime())
	results = get_vehicle_availability_map(
		vehicle_names=[vehicle],
		from_time=at_time,
		to_time=add_to_date(at_time, seconds=1),
	)
	return results.get(vehicle) or _missing_vehicle_result(vehicle)


def is_vehicle_available(
	vehicle,
	from_time,
	to_time,
	required_vehicle_category=None,
	delivery_load=None,
	exclude_allocation=None,
):
	return evaluate_vehicle_availability(
		vehicle,
		from_time,
		to_time,
		required_vehicle_category=required_vehicle_category,
		delivery_load=delivery_load,
		exclude_allocation=exclude_allocation,
	).dispatch_eligible


def evaluate_vehicle_availability(
	vehicle,
	from_time,
	to_time,
	*,
	required_vehicle_category=None,
	delivery_load=None,
	exclude_allocation=None,
):
	"""Evaluate one Vehicle using the same bulk engine used by planning screens."""
	load_name = _document_name(delivery_load)
	results = get_vehicle_availability_map(
		vehicle_names=[vehicle],
		from_time=from_time,
		to_time=to_time,
		expected_company=getattr(delivery_load, "company", None),
		required_vehicle_category=required_vehicle_category,
		delivery_load=delivery_load,
		exclude_allocation=exclude_allocation,
		exclude_delivery_load=load_name,
	)
	return results.get(vehicle) or _missing_vehicle_result(vehicle)


def get_available_vehicles(
	vehicle_category,
	from_time,
	to_time,
	*,
	company=None,
	delivery_load=None,
):
	results = get_vehicle_availability_map(
		company=company,
		vehicle_category=vehicle_category,
		from_time=from_time,
		to_time=to_time,
		delivery_load=delivery_load,
	)
	return [result for result in results.values() if result.dispatch_eligible]


def get_vehicle_category_availability(
	company,
	from_time,
	to_time,
	*,
	delivery_method=None,
):
	"""Return category counts and vehicle details from one preloaded evaluation."""
	results = get_vehicle_availability_map(
		company=company,
		from_time=from_time,
		to_time=to_time,
	)
	summaries = defaultdict(_empty_category_summary)
	for result in results.values():
		category = result.vehicle_category
		if not category:
			continue
		summary = summaries[category]
		summary["vehicle_category"] = category
		summary["vehicle_category_label"] = result.vehicle_category_label
		summary["total"] += 1
		summary[_state_key(result.operational_state)] += 1
		summary["vehicles"].append(result)
	return {
		"categories": dict(summaries),
		"fleet_tracked": bool(results),
		"from_time": from_time,
		"to_time": to_time,
	}


def get_vehicle_availability_map(
	*,
	vehicle_names=None,
	company=None,
	vehicle_category=None,
	from_time=None,
	to_time=None,
	required_vehicle_category=None,
	delivery_load=None,
	exclude_allocation=None,
	exclude_delivery_load=None,
	expected_company=None,
):
	"""Bulk-load operational records and evaluate every matching Vehicle once."""
	from_time, to_time = _normalize_interval(from_time, to_time)
	filters = {}
	if vehicle_names:
		filters["name"] = ["in", list(dict.fromkeys(vehicle_names))]
	if company and not vehicle_names:
		filters["company"] = ["in", [company, "", None]]
	if vehicle_category:
		filters["vuna_vehicle_category"] = vehicle_category
	vehicles = frappe.get_all(
		"Vehicle",
		filters=filters,
		fields=[
			"name",
			"license_plate",
			"company",
			"vuna_vehicle_category",
			"vuna_transport_enabled",
		],
		order_by="license_plate asc, name asc",
	)
	if not vehicles:
		return {}

	vehicle_names = [vehicle.name for vehicle in vehicles]
	allocations = frappe.get_all(
		"Vehicle Allocation",
		filters={
			"vehicle": ["in", vehicle_names],
			"docstatus": 1,
			"status": ["in", ACTIVE_ALLOCATION_STATUSES],
		},
		fields=[
			"name",
			"vehicle",
			"delivery_load",
			"effective_from",
			"effective_to",
		],
	)
	trips = frappe.get_all(
		"Delivery Trip",
		filters={"docstatus": ["!=", 2], "status": ["in", ACTIVE_TRIP_STATUSES]},
		fields=[
			"name",
			"vehicle",
			"vuna_current_vehicle",
			"vuna_delivery_load",
			"departure_time",
			"status",
		],
	)
	trips = [
		trip
		for trip in trips
		if (trip.vuna_current_vehicle or trip.vehicle) in vehicle_names
	]
	load_names = {
		row.delivery_load for row in allocations if row.delivery_load
	} | {row.vuna_delivery_load for row in trips if row.vuna_delivery_load}
	loads = {
		row.name: row
		for row in frappe.get_all(
			"Delivery Load",
			filters={"name": ["in", list(load_names)]},
			fields=[
				"name",
				"status",
				"planned_departure",
				"planned_arrival",
			],
		)
	} if load_names else {}

	allocations_by_vehicle = defaultdict(list)
	for allocation in allocations:
		if allocation.name == exclude_allocation:
			continue
		if exclude_delivery_load and allocation.delivery_load == exclude_delivery_load:
			continue
		allocations_by_vehicle[allocation.vehicle].append(allocation)
	trips_by_vehicle = defaultdict(list)
	for trip in trips:
		if exclude_delivery_load and trip.vuna_delivery_load == exclude_delivery_load:
			continue
		trips_by_vehicle[trip.vuna_current_vehicle or trip.vehicle].append(trip)

	compatible_categories = _get_compatible_categories(
		required_vehicle_category, delivery_load
	)
	results = {
		vehicle.name: _evaluate_vehicle(
			vehicle,
			allocations_by_vehicle.get(vehicle.name, []),
			trips_by_vehicle.get(vehicle.name, []),
			loads,
			from_time,
			to_time,
			expected_company or company,
			compatible_categories,
		)
		for vehicle in vehicles
	}
	category_names = {
		result.vehicle_category for result in results.values() if result.vehicle_category
	}
	category_labels = dict(
		frappe.get_all(
			"Vehicle Category",
			filters={"name": ["in", list(category_names)]},
			fields=["name", "vehicle_category_name"],
			as_list=True,
		)
	) if category_names else {}
	for result in results.values():
		result.vehicle_category_label = (
			category_labels.get(result.vehicle_category) or result.vehicle_category
		)
	return results


def _evaluate_vehicle(
	vehicle,
	allocations,
	trips,
	loads,
	from_time,
	to_time,
	company,
	compatible_categories,
):
	reasons = []
	current_allocation = None
	current_trip = None
	next_available_at = None
	state = "Available"

	if not vehicle.vuna_transport_enabled:
		reasons.append(_reason("TRANSPORT_DISABLED", _("Vehicle is not enabled for transport planning.")))
	if company and vehicle.company and vehicle.company != company:
		reasons.append(
			_reason(
				"COMPANY_MISMATCH",
				_("Vehicle belongs to Company {0}.").format(vehicle.company),
			)
		)
	if not vehicle.vuna_vehicle_category:
		reasons.append(_reason("CATEGORY_MISSING", _("Vehicle Category is not configured.")))
	elif compatible_categories is not None and vehicle.vuna_vehicle_category not in compatible_categories:
		reasons.append(
			_reason(
				"CATEGORY_INCOMPATIBLE",
				_("Vehicle Category {0} is not compatible with this load.").format(
					vehicle.vuna_vehicle_category
				),
			)
		)

	for trip in trips:
		load = loads.get(trip.vuna_delivery_load)
		trip_from, trip_to = _trip_window(trip, load)
		if trip.status != "In Transit" and not interval_overlaps(
			from_time, to_time, trip_from, trip_to
		):
			continue
		current_trip = current_trip or trip.name
		state = "In Trip"
		reasons.append(
			_reason(
				"ACTIVE_TRIP",
				_("Vehicle is assigned to active Delivery Trip {0}.").format(trip.name),
				"Delivery Trip",
				trip.name,
			)
		)
		if trip.status != "In Transit":
			next_available_at = _latest_datetime(next_available_at, trip_to)

	for allocation in allocations:
		load = loads.get(allocation.delivery_load)
		allocation_from = load.planned_departure if load else allocation.effective_from
		allocation_to = load.planned_arrival if load else allocation.effective_to
		if not interval_overlaps(
			from_time, to_time, allocation_from, allocation_to
		):
			continue
		current_allocation = current_allocation or allocation.name
		if state != "In Trip":
			state = _allocation_state(load)
		reasons.append(
			_reason(
				"ACTIVE_ALLOCATION",
				_("Vehicle is allocated to Delivery Load {0}.").format(
					allocation.delivery_load
				),
				"Vehicle Allocation",
				allocation.name,
			)
		)
		next_available_at = _latest_datetime(next_available_at, allocation_to)

	if reasons and state == "Available":
		state = "Unavailable"
	return frappe._dict(
		vehicle=vehicle.name,
		license_plate=vehicle.license_plate or vehicle.name,
		vehicle_category=vehicle.vuna_vehicle_category,
		vehicle_category_label=vehicle.vuna_vehicle_category,
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
		"Loading": "ACTIVE_ALLOCATION",
		"Ready for Dispatch": "ACTIVE_ALLOCATION",
	}.get(state)
	if preferred_code:
		match = next((reason for reason in reasons if reason.code == preferred_code), None)
		if match:
			return match.message
	return reasons[0].message


def _get_compatible_categories(required_vehicle_category, delivery_load):
	if delivery_load:
		from vuna_transport.services.delivery_load_service import (
			get_compatible_vehicle_category_names,
		)

		return set(get_compatible_vehicle_category_names(delivery_load))
	if required_vehicle_category:
		return {required_vehicle_category}
	return None


def _trip_window(trip, load):
	if load and load.planned_departure and load.planned_arrival:
		return load.planned_departure, load.planned_arrival
	departure = trip.departure_time
	return departure, add_days(departure, 1) if departure else None


def _allocation_state(load):
	if load and load.status == "Loading":
		return "Loading"
	if load and load.status == "Ready for Dispatch":
		return "Ready for Dispatch"
	return "Allocated"


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
	if from_time >= to_time:
		frappe.throw(_("Availability end time must be after start time."))
	return from_time, to_time


def _latest_datetime(current, candidate):
	if not candidate:
		return current
	candidate = get_datetime(candidate)
	return candidate if not current or candidate > get_datetime(current) else current


def _document_name(document):
	return document if isinstance(document, str) else getattr(document, "name", None)


def _missing_vehicle_result(vehicle):
	reason = _reason("VEHICLE_NOT_FOUND", _("Vehicle {0} does not exist.").format(vehicle))
	return frappe._dict(
		vehicle=vehicle,
		license_plate=vehicle,
		vehicle_category=None,
		vehicle_category_label=None,
		operational_state="Unavailable",
		dispatch_eligible=False,
		primary_reason=reason.message,
		reasons=[reason],
		current_allocation=None,
		current_trip=None,
		next_available_at=None,
	)


def _empty_category_summary():
	return {
		"vehicle_category": None,
		"vehicle_category_label": None,
		"total": 0,
		"available": 0,
		"allocated": 0,
		"loading": 0,
		"ready_for_dispatch": 0,
		"in_trip": 0,
		"unavailable": 0,
		"vehicles": [],
	}


def _state_key(state):
	return state.lower().replace(" ", "_")
