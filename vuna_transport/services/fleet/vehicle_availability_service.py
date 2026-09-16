"""Canonical, explainable physical Vehicle availability."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, get_datetime, now_datetime

from vuna_transport.services.fleet.vehicle_compliance_service import (
	get_vehicle_compliance_map,
)
from vuna_transport.services.fleet.vehicle_owner_service import normalize_vehicle_owner
from vuna_transport.services.shared.resource_scheduling_service import interval_overlaps

ACTIVE_ALLOCATION_STATUSES = ("Allocated", "Accepted")
ACTIVE_TRIP_STATUSES = ("Scheduled", "In Transit")
VEHICLE_STATES = (
	"Available",
	"Allocated",
	"Loading",
	"Ready for Dispatch",
	"In Trip",
	"Under Maintenance",
	"Unavailable",
)

NON_OPERATIONAL_ASSET_STATUSES = (
	"Draft",
	"Cancelled",
	"Sold",
	"Scrapped",
	"Work In Progress",
)
MAINTENANCE_ASSET_STATUSES = ("In Maintenance", "Out of Order")


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
		if result.compliance_status == "Blocked":
			summary["compliance_blocked"] += 1
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
			"vuna_disabled",
			"vuna_vehicle_owner",
			"vuna_asset",
			"policy_no",
			"start_date",
			"end_date",
		],
		order_by="license_plate asc, name asc",
	)
	if not vehicles:
		return {}

	vehicle_names = [vehicle.name for vehicle in vehicles]
	asset_names = {vehicle.vuna_asset for vehicle in vehicles if vehicle.vuna_asset}
	assets = (
		{
			asset.name: asset
			for asset in frappe.get_all(
				"Asset",
				filters={"name": ["in", list(asset_names)]},
				fields=["name", "company", "status", "docstatus"],
			)
		}
		if asset_names
		else {}
	)
	asset_repairs = (
		frappe.get_all(
			"Asset Repair",
			filters={
				"asset": ["in", list(asset_names)],
				"docstatus": ["!=", 2],
				"repair_status": "Pending",
			},
			fields=["name", "asset", "repair_status", "failure_date"],
		)
		if asset_names
		else []
	)
	repairs_by_asset = defaultdict(list)
	for repair in asset_repairs:
		repairs_by_asset[repair.asset].append(repair)
	compliance_by_vehicle = get_vehicle_compliance_map(vehicles, to_time)
	availability_blocks = frappe.get_all(
		"Vehicle Availability Block",
		filters={
			"vehicle": ["in", vehicle_names],
			"docstatus": 1,
			"from_datetime": ["<", to_time],
			"to_datetime": [">", from_time],
		},
		fields=[
			"name",
			"vehicle",
			"from_datetime",
			"to_datetime",
			"reason",
			"reference_doctype",
			"reference_name",
		],
		order_by="from_datetime asc",
	)
	blocks_by_vehicle = defaultdict(list)
	for block in availability_blocks:
		blocks_by_vehicle[block.vehicle].append(block)
	incidents = []
	if frappe.db.table_exists("Vehicle Incident"):
		incidents = frappe.get_all(
			"Vehicle Incident",
			filters={
				"vehicle": ["in", vehicle_names],
				"docstatus": ["!=", 2],
				"resolution_status": ["not in", ["Resolved", "Closed"]],
			},
			fields=[
				"name",
				"vehicle",
				"incident_type",
				"incident_datetime",
				"severity",
				"vehicle_immobilized",
				"resolution_status",
			],
			order_by="incident_datetime desc",
		)
	incidents_by_vehicle = defaultdict(list)
	for incident in incidents:
		incidents_by_vehicle[incident.vehicle].append(incident)

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
			{"vehicle": ["in", vehicle_names]},
			{"vuna_current_vehicle": ["in", vehicle_names]},
		],
		fields=[
			"name",
			"vehicle",
			"vuna_current_vehicle",
			"departure_time",
			"status",
		],
	)
	trips = [trip for trip in trips if (trip.vuna_current_vehicle or trip.vehicle) in vehicle_names]
	_attach_trip_loads(trips)
	load_names = {
		load_name for row in allocations for load_name in row.delivery_loads if load_name
	} | {
		load_name for row in trips for load_name in row.delivery_loads
	}
	loads = (
		{
			row.name: row
			for row in frappe.get_all(
				"Delivery Load",
				filters={"name": ["in", list(load_names)], "docstatus": ["!=", 2]},
				fields=[
					"name",
					"status",
					"planned_departure",
					"planned_arrival",
				],
			)
		}
		if load_names
		else {}
	)

	allocations_by_vehicle = defaultdict(list)
	for allocation in allocations:
		if allocation.name == exclude_allocation:
			continue
		if exclude_delivery_load and exclude_delivery_load in allocation.delivery_loads:
			continue
		allocations_by_vehicle[allocation.vehicle].append(allocation)
	trips_by_vehicle = defaultdict(list)
	for trip in trips:
		if exclude_delivery_load and exclude_delivery_load in trip.delivery_loads:
			continue
		trips_by_vehicle[trip.vuna_current_vehicle or trip.vehicle].append(trip)

	compatible_categories = _get_compatible_categories(required_vehicle_category, delivery_load)
	results = {
		vehicle.name: _evaluate_vehicle(
			vehicle,
			assets.get(vehicle.vuna_asset),
			repairs_by_asset.get(vehicle.vuna_asset, []),
			compliance_by_vehicle.get(vehicle.name),
			blocks_by_vehicle.get(vehicle.name, []),
			incidents_by_vehicle.get(vehicle.name, []),
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
	category_names = {result.vehicle_category for result in results.values() if result.vehicle_category}
	category_labels = (
		dict(
			frappe.get_all(
				"Vehicle Category",
				filters={"name": ["in", list(category_names)]},
				fields=["name", "vehicle_category_name"],
				as_list=True,
			)
		)
		if category_names
		else {}
	)
	for result in results.values():
		result.vehicle_category_label = (
			category_labels.get(result.vehicle_category) or result.vehicle_category
		)
	return results


def _evaluate_vehicle(
	vehicle,
	asset,
	asset_repairs,
	compliance,
	availability_blocks,
	incidents,
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
	if getattr(vehicle, "vuna_disabled", 0):
		reasons.append(_reason("VEHICLE_DISABLED", _("Vehicle is globally disabled.")))
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

	asset_name = getattr(vehicle, "vuna_asset", None)
	vehicle_owner = normalize_vehicle_owner(getattr(vehicle, "vuna_vehicle_owner", None))
	if vehicle.vuna_transport_enabled and vehicle_owner == "Company" and not asset_name:
		reasons.append(
			_reason(
				"ASSET_REQUIRED",
				_("Company-owned transport Vehicle must be linked to an ERPNext Asset."),
			)
		)
	elif asset_name and not asset:
		reasons.append(
			_reason(
				"ASSET_NOT_FOUND",
				_("Linked Asset {0} does not exist.").format(asset_name),
				"Asset",
				asset_name,
			)
		)
	elif asset:
		if vehicle.company and asset.company and vehicle.company != asset.company:
			reasons.append(
				_reason(
					"ASSET_COMPANY_MISMATCH",
					_("Linked Asset {0} belongs to Company {1}.").format(asset.name, asset.company),
					"Asset",
					asset.name,
				)
			)
		if asset.docstatus != 1 or asset.status in NON_OPERATIONAL_ASSET_STATUSES:
			reasons.append(
				_reason(
					"ASSET_NOT_OPERATIONAL",
					_("Linked Asset {0} is {1}.").format(asset.name, asset.status or _("not submitted")),
					"Asset",
					asset.name,
				)
			)
		elif asset.status in MAINTENANCE_ASSET_STATUSES:
			reasons.append(
				_reason(
					"ASSET_IN_MAINTENANCE" if asset.status == "In Maintenance" else "ASSET_OUT_OF_ORDER",
					_("Linked Asset {0} is {1}.").format(asset.name, asset.status),
					"Asset",
					asset.name,
				)
			)
		for repair in asset_repairs:
			reasons.append(
				_reason(
					"ASSET_REPAIR_PENDING",
					_("Pending Asset Repair {0}.").format(repair.name),
					"Asset Repair",
					repair.name,
				)
			)

	master_blocked = any(
		reason.code
		in {
			"TRANSPORT_DISABLED",
			"VEHICLE_DISABLED",
			"COMPANY_MISMATCH",
			"CATEGORY_MISSING",
			"CATEGORY_INCOMPATIBLE",
			"ASSET_REQUIRED",
			"ASSET_NOT_FOUND",
			"ASSET_COMPANY_MISMATCH",
			"ASSET_NOT_OPERATIONAL",
		}
		for reason in reasons
	)
	maintenance_blocked = any(
		reason.code in {"ASSET_IN_MAINTENANCE", "ASSET_OUT_OF_ORDER", "ASSET_REPAIR_PENDING"}
		for reason in reasons
	)
	if master_blocked:
		state = "Unavailable"
	elif maintenance_blocked:
		state = "Under Maintenance"

	for trip in trips:
		trip_from, trip_to = _trip_window(trip, [loads.get(name) for name in trip.delivery_loads])
		if trip.status != "In Transit" and not interval_overlaps(from_time, to_time, trip_from, trip_to):
			continue
		current_trip = current_trip or trip.name
		if not master_blocked:
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

	for block in availability_blocks:
		if state == "Available":
			state = "Unavailable"
		reasons.append(
			_reason(
				"AVAILABILITY_BLOCK",
				_("Vehicle is blocked for {0} until {1}.").format(block.reason, block.to_datetime),
				"Vehicle Availability Block",
				block.name,
			)
		)
		next_available_at = _latest_datetime(next_available_at, block.to_datetime)

	blocking_incidents = [
		incident
		for incident in incidents
		if incident.vehicle_immobilized and incident.resolution_status not in ("Resolved", "Closed")
	]
	for incident in blocking_incidents:
		if state == "Available":
			state = "Unavailable"
		reasons.append(
			_reason(
				"IMMOBILIZING_INCIDENT",
				_("Open immobilizing Vehicle Incident {0}: {1}.").format(
					incident.name, incident.incident_type
				),
				"Vehicle Incident",
				incident.name,
			)
		)

	for allocation in allocations:
		allocation_load_names = _allocation_load_names(allocation)
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
		current_allocation = current_allocation or allocation.name
		if state == "Available":
			state = "Loading" if any(load.status == "Loading" for load in allocation_loads) else _allocation_state(
				allocation_loads[0] if allocation_loads else None
			)
		reasons.append(
			_reason(
				"ACTIVE_ALLOCATION",
				_("Vehicle is allocated to Delivery Load(s) {0}.").format(", ".join(allocation_load_names)),
				"Vehicle Allocation",
				allocation.name,
			)
		)
		next_available_at = _latest_datetime(next_available_at, allocation_to)

	if compliance and not compliance.compliant:
		reasons.extend(compliance.reasons)
		if state == "Available":
			state = "Unavailable"

	if reasons and state == "Available":
		state = "Unavailable"
	return frappe._dict(
		vehicle=vehicle.name,
		license_plate=vehicle.license_plate or vehicle.name,
		vehicle_category=vehicle.vuna_vehicle_category,
		vehicle_category_label=vehicle.vuna_vehicle_category,
		linked_asset=asset_name,
		asset_status=getattr(asset, "status", None),
		active_asset_repair=asset_repairs[0].name if asset_repairs else None,
		current_availability_block=(availability_blocks[0].name if availability_blocks else None),
		current_vehicle_incident=(blocking_incidents[0].name if blocking_incidents else None),
		compliance_status=compliance.status if compliance else "Compliant",
		compliance_reasons=compliance.reasons if compliance else [],
		compliance_warnings=compliance.warnings if compliance else [],
		operational_state=state,
		dispatch_eligible=not reasons,
		primary_reason=_primary_reason(reasons, state),
		reasons=reasons,
		current_allocation=current_allocation,
		current_trip=current_trip,
		next_available_at=next_available_at,
	)


def _allocation_load_names(allocation):
	"""Read canonical allocation child rows, with a compatibility fallback for test/domain stubs."""
	return list(
		getattr(allocation, "delivery_loads", None)
		or ([allocation.delivery_load] if getattr(allocation, "delivery_load", None) else [])
	)


def _primary_reason(reasons, state):
	if not reasons:
		return None
	preferred_code = {
		"In Trip": "ACTIVE_TRIP",
		"Under Maintenance": "ASSET_REPAIR_PENDING",
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
		from vuna_transport.services.planning.delivery_load_service import (
			get_compatible_vehicle_category_names,
		)

		return set(get_compatible_vehicle_category_names(delivery_load))
	if required_vehicle_category:
		return {required_vehicle_category}
	return None


def _attach_trip_loads(trips):
	if not trips:
		return
	load_rows = frappe.get_all(
		"Delivery Stop",
		filters={"parent": ["in", [trip.name for trip in trips]], "parenttype": "Delivery Trip"},
		fields=["parent", "vuna_delivery_load"],
	)
	loads_by_trip = defaultdict(list)
	for row in load_rows:
		if row.vuna_delivery_load:
			loads_by_trip[row.parent].append(row.vuna_delivery_load)
	for trip in trips:
		trip.delivery_loads = loads_by_trip.get(trip.name, [])


def _trip_window(trip, loads):
	loads = [load for load in loads if load]
	departures = [get_datetime(load.planned_departure) for load in loads if load.planned_departure]
	arrivals = [get_datetime(load.planned_arrival) for load in loads if load.planned_arrival]
	if departures and arrivals:
		return min(departures), max(arrivals)
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


def _document_name(document):
	return document if isinstance(document, str) else getattr(document, "name", None)


def _missing_vehicle_result(vehicle):
	reason = _reason("VEHICLE_NOT_FOUND", _("Vehicle {0} does not exist.").format(vehicle))
	return frappe._dict(
		vehicle=vehicle,
		license_plate=vehicle,
		vehicle_category=None,
		vehicle_category_label=None,
		linked_asset=None,
		asset_status=None,
		active_asset_repair=None,
		current_availability_block=None,
		current_vehicle_incident=None,
		compliance_status="Blocked",
		compliance_reasons=[reason],
		compliance_warnings=[],
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
		"under_maintenance": 0,
		"compliance_blocked": 0,
		"unavailable": 0,
		"vehicles": [],
	}


def _state_key(state):
	return state.lower().replace(" ", "_")
