"""Vehicle allocation validation, activation, and history services."""

import frappe
from frappe import _
from frappe.utils import add_to_date, flt, get_datetime, now_datetime

from vuna_transport.services.demand.delivery_method_service import (
	validate_delivery_method_allows_vehicle_allocation,
)
from vuna_transport.services.execution.dispatch_guard_service import (
	lock_document,
	set_delivery_load_status,
	validate_load_is_operational,
)
from vuna_transport.services.fleet.driver_availability_service import (
	evaluate_driver_availability,
)
from vuna_transport.services.fleet.vehicle_availability_service import (
	evaluate_vehicle_availability,
)
from vuna_transport.services.shared.resource_scheduling_service import time_windows_overlap
from vuna_transport.services.shared.security_service import (
	require_doctype_permission,
	require_document_permission,
)
from vuna_transport.services.shared.workflow_policy_service import (
	allocation_is_required_before_delivery_notes,
)

ACTIVE_STATUSES = (
	"Allocated",
	"Accepted",
	"Awaiting Delivery Notes",
	"Ready for Delivery Trip",
	"Delivery Trip Draft",
	"Delivery Trip Scheduled",
	"In Transit",
)


@frappe.whitelist()
def get_allocation_load_context(delivery_loads=None):
	"""Return the shared form context for currently selected load rows."""
	if isinstance(delivery_loads, str):
		delivery_loads = frappe.parse_json(delivery_loads)
	names = list(dict.fromkeys(delivery_loads or []))
	if not names:
		return {"company": None, "required_vehicle_category": None, "planned_reporting_time": None}
	loads = [frappe.get_doc("Delivery Load", name) for name in names]
	for load in loads:
		require_document_permission(load, "read")
	_validate_selected_load_compatibility(loads)
	combined = _build_combined_capacity_load(loads)
	capacity = None
	if loads[0].required_vehicle_category:
		from vuna_transport.services.planning.delivery_load_service import (
			get_vehicle_category_capacity_summary,
		)

		capacity = get_vehicle_category_capacity_summary(
			loads[0].required_vehicle_category,
			{
				"weight": combined.total_weight,
				"volume": combined.total_volume,
				"pallets": combined.total_pallets,
				"cases": combined.total_cases,
				"crates": combined.total_crates,
			},
			combined.weight_uom,
			combined.volume_uom,
		)
	return {
		"company": loads[0].company,
		"required_vehicle_category": loads[0].required_vehicle_category,
		"planned_reporting_time": _earliest_planned_departure(loads),
		"totals": {
			"weight": combined.total_weight,
			"weight_uom": combined.weight_uom,
			"volume": combined.total_volume,
			"volume_uom": combined.volume_uom,
			"stops": sum(flt(load.total_stops) for load in loads),
		},
		"capacity": capacity,
	}


def get_allocation_load_names(allocation):
	"""Return the canonical Delivery Load set for an allocation."""
	rows = getattr(allocation, "delivery_loads", None) or []
	names = [row.delivery_load for row in rows if getattr(row, "delivery_load", None)]
	if not names and getattr(allocation, "delivery_load", None):
		names = [allocation.delivery_load]
	return list(dict.fromkeys(names))


def get_allocation_loads(allocation, *, lock=False):
	"""Return selected Delivery Loads, optionally locking each for an operation."""
	child_names = [
		row.delivery_load
		for row in (getattr(allocation, "delivery_loads", None) or [])
		if getattr(row, "delivery_load", None)
	]
	if len(child_names) != len(set(child_names)):
		frappe.throw(_("A Delivery Load can only be added once to a Vehicle Allocation."))
	names = get_allocation_load_names(allocation)
	if not names:
		frappe.throw(_("Add at least one Delivery Load to the Vehicle Allocation."))
	loads = []
	for name in names:
		if lock:
			lock_document("Delivery Load", name)
		loads.append(frappe.get_doc("Delivery Load", name))
	return loads


def validate_allocation_load_set(load_names, vehicle_category=None):
	"""Validate candidate loads without creating or reserving an allocation."""
	names = list(dict.fromkeys(load_names or []))
	names = [name for name in names if name]
	loads = [frappe.get_doc("Delivery Load", name) for name in names]
	_validate_selected_load_compatibility(loads)
	if vehicle_category:
		_validate_vehicle_category_for_loads(loads, vehicle_category)
	return loads


def allocation_includes_load(allocation, delivery_load):
	return delivery_load in set(get_allocation_load_names(allocation))


def get_allocation_names_for_load(delivery_load, *, docstatus=None):
	"""Find allocation history through the canonical child table."""
	parents = frappe.get_all(
		"Vehicle Allocation Load",
		filters={"delivery_load": delivery_load, "parenttype": "Vehicle Allocation"},
		pluck="parent",
	)
	parents = list(dict.fromkeys(parents))
	if docstatus is None or not parents:
		return parents
	return frappe.get_all(
		"Vehicle Allocation", filters={"name": ["in", parents], "docstatus": docstatus}, pluck="name"
	)


@frappe.whitelist()
def make_vehicle_allocation_from_load(source_name):
	require_doctype_permission("Vehicle Allocation", "create")
	lock_document("Delivery Load", source_name)
	load = frappe.get_doc("Delivery Load", source_name)
	require_document_permission(load, "write")
	validate_load_is_operational(load, _("allocating a vehicle"))
	_validate_planning_window(load)
	_validate_initial_allocation_sequence(load)
	existing_draft = _get_draft_allocation_for_load(load.name)
	if existing_draft:
		return frappe.get_doc("Vehicle Allocation", existing_draft)
	allocation = frappe.new_doc("Vehicle Allocation")
	_append_load(allocation, load)
	allocation.company = load.company
	if load.required_vehicle_category:
		allocation.required_vehicle_category = load.required_vehicle_category
	allocation.planned_reporting_time = load.planned_departure
	allocation.allocation_type = "Owned Fleet"
	return allocation


@frappe.whitelist()
def make_replacement_allocation_from_load(source_name):
	"""Prepare an auditable replacement for the load's current allocation."""
	return make_replacement_allocation(source_name)


@frappe.whitelist()
def make_delivery_trip_from_allocation(source_name):
	"""Return a prepared, unsaved Delivery Trip for all loads in an allocation."""
	allocation = frappe.get_doc("Vehicle Allocation", source_name)
	require_document_permission(allocation, "read")
	if allocation.docstatus != 1:
		frappe.throw(_("Submit the Vehicle Allocation before creating a Delivery Trip."))
	load_names = get_allocation_load_names(allocation)
	if not load_names:
		frappe.throw(_("Add at least one Delivery Load before creating a Delivery Trip."))

	from vuna_transport.services.integrations.erpnext_integration import create_delivery_trip_for_load

	return create_delivery_trip_for_load(load_names[0])


@frappe.whitelist()
def make_replacement_allocation_from_trip(source_name):
	trip = frappe.get_doc("Delivery Trip", source_name)
	require_document_permission(trip, "read")
	from vuna_transport.services.execution.lineage_service import get_delivery_trip_load_names

	load_names = get_delivery_trip_load_names(trip)
	if not load_names:
		frappe.throw(_("Delivery Trip {0} is not linked to a Delivery Load.").format(trip.name))
	return make_replacement_allocation(load_names)


def make_replacement_allocation(delivery_load):
	"""Build a replacement through the canonical reallocation workflow."""
	return _make_replacement_allocation(delivery_load)


def _make_replacement_allocation(delivery_load):
	require_doctype_permission("Vehicle Allocation", "create")
	load_names = list(
		dict.fromkeys(delivery_load if isinstance(delivery_load, (list, tuple, set)) else [delivery_load])
	)
	if not load_names:
		frappe.throw(_("Select at least one Delivery Load to reallocate."))
	for load_name in load_names:
		lock_document("Delivery Load", load_name)
	loads = [frappe.get_doc("Delivery Load", load_name) for load_name in load_names]
	for load in loads:
		require_document_permission(load, "write")
		validate_load_is_operational(load, _("reallocating a vehicle"))
		_validate_planning_window(load)
	previous = get_active_allocation(loads[0], required=True)
	if any(get_active_allocation(load, required=True).name != previous.name for load in loads):
		frappe.throw(_("All selected Delivery Loads must belong to the same active Vehicle Allocation."))
	trip = _get_active_trip(loads[0])
	_validate_trip_allows_reallocation(trip)
	previous_load_names = set(get_allocation_load_names(previous))
	selected_load_names = {load.name for load in loads}
	if trip and selected_load_names != previous_load_names:
		frappe.throw(
			_(
				"This Delivery Trip contains multiple loads. Reallocate the complete trip allocation, "
				"or amend the trip stops before splitting individual loads."
			)
		)
	existing_draft = frappe.db.get_value(
		"Vehicle Allocation",
		{"replacement_of": previous.name, "docstatus": 0},
		"name",
		order_by="creation desc",
	)
	if existing_draft:
		return frappe.get_doc("Vehicle Allocation", existing_draft)

	allocation = frappe.new_doc("Vehicle Allocation")
	for selected_load in loads:
		_append_load(allocation, selected_load)
	allocation.company = loads[0].company
	allocation.required_vehicle_category = loads[0].required_vehicle_category
	allocation.planned_reporting_time = loads[0].planned_departure
	allocation.allocation_type = previous.allocation_type
	allocation.assignment_event = "Reallocation"
	allocation.replacement_of = previous.name
	allocation.replacement_stage = _get_replacement_stage(trip)
	if previous.allocation_type == "Subcontractor":
		allocation.supplier = previous.supplier
	return allocation


def validate_allocation(allocation):
	loads = get_allocation_loads(allocation, lock=True)
	_prepare_allocation_load_rows(allocation, loads)
	load = loads[0]
	for selected_load in loads:
		require_document_permission(selected_load, "write")
		validate_load_is_operational(selected_load, _("allocating a vehicle"))
		_validate_planning_window(selected_load)
		_validate_initial_allocation_sequence(selected_load, allocation)
		_validate_trip_allows_reallocation(_get_active_trip(selected_load))
		validate_delivery_method_allows_vehicle_allocation(selected_load.delivery_method)
	_validate_selected_load_compatibility(loads)

	allocation.company = load.company
	selected_category = allocation.required_vehicle_category
	if load.required_vehicle_category:
		if selected_category and selected_category != load.required_vehicle_category:
			frappe.throw(
				_(
					"Selected Vehicle Category {0} does not match Delivery Load {1}'s required category {2}."
				).format(selected_category, load.name, load.required_vehicle_category)
			)
		allocation.required_vehicle_category = load.required_vehicle_category
	allocation.allocated_by = allocation.allocated_by or frappe.session.user
	allocation.allocated_on = allocation.allocated_on or now_datetime()
	allocation.planned_reporting_time = allocation.planned_reporting_time or _earliest_planned_departure(
		loads
	)
	_set_assignment_context(allocation, loads)
	_derive_vehicle_source(allocation)

	if allocation.allocation_type == "Owned Fleet":
		_validate_owned_fleet(allocation, loads)
	elif allocation.allocation_type == "Subcontractor":
		_validate_subcontractor(allocation, loads)
	else:
		frappe.throw(_("Select a valid Allocation Type."))
	_validate_replacement_change(allocation)


def _set_assignment_context(allocation, loads):
	active_names = {load.active_vehicle_allocation for load in loads if load.active_vehicle_allocation}
	if len(active_names) > 1:
		frappe.throw(_("Selected Delivery Loads are assigned to different active Vehicle Allocations."))
	active_name = next(iter(active_names), None)
	trip = _get_active_trip(loads[0])
	if active_name and active_name != allocation.name:
		if allocation.replacement_of and allocation.replacement_of != active_name:
			frappe.throw(
				_(
					"The active Vehicle Allocation changed from {0} to {1}. Refresh and prepare a new replacement."
				).format(allocation.replacement_of, active_name)
			)
		allocation.assignment_event = "Reallocation"
		allocation.replacement_of = active_name
		allocation.replacement_stage = _get_replacement_stage(trip)
		if not allocation.replacement_reason:
			frappe.throw(_("Select a Replacement Reason."))
		if not (allocation.replacement_details or "").strip():
			frappe.throw(_("Describe why the vehicle or driver is being replaced."))
	else:
		allocation.assignment_event = "Initial Assignment"
		allocation.replacement_of = None
		allocation.replacement_stage = None
		allocation.replacement_reason = None
		allocation.replacement_details = None


def _validate_replacement_change(allocation):
	if allocation.assignment_event != "Reallocation" or not allocation.replacement_of:
		return
	previous = frappe.get_doc("Vehicle Allocation", allocation.replacement_of)
	selected_load_names = set(get_allocation_load_names(allocation))
	previous_load_names = set(get_allocation_load_names(previous))
	if not selected_load_names.issubset(previous_load_names):
		frappe.throw(
			_("A replacement Vehicle Allocation can only contain loads from the original allocation.")
		)
	if selected_load_names != previous_load_names:
		loads = get_allocation_loads(allocation)
		if loads and _get_active_trip(loads[0]):
			frappe.throw(
				_(
					"Individual loads cannot be split from an active Delivery Trip yet. "
					"Reallocate the complete trip allocation."
				)
			)
	if previous.docstatus != 1 or previous.status not in ACTIVE_STATUSES:
		frappe.throw(_("Vehicle Allocation {0} is no longer active. Refresh the load.").format(previous.name))
	if allocation.vehicle and allocation.vehicle == previous.vehicle and allocation.driver == previous.driver:
		frappe.throw(_("Select a different vehicle or driver for the replacement allocation."))


def _validate_owned_fleet(allocation, loads):
	loads = _coerce_loads(loads)
	if not allocation.vehicle or not allocation.driver:
		frappe.throw(_("Vehicle and Driver are required for an Owned Fleet allocation."))

	lock_document("Vehicle", allocation.vehicle)
	lock_document("Driver", allocation.driver)
	vehicle = frappe.get_doc("Vehicle", allocation.vehicle)
	allocation.vehicle_owner = getattr(vehicle, "vuna_vehicle_owner", None)
	_validate_vehicle_category_for_loads(loads, vehicle.vuna_vehicle_category)
	_validate_vehicle_is_available(allocation, loads)
	_validate_driver_is_available(allocation, loads)


def _validate_subcontractor(allocation, loads):
	loads = _coerce_loads(loads)
	if not allocation.vehicle or not allocation.driver:
		frappe.throw(_("Vehicle and Driver records are required for a Subcontractor allocation."))
	lock_document("Vehicle", allocation.vehicle)
	vehicle = frappe.get_doc("Vehicle", allocation.vehicle)
	allocation.vehicle_owner = getattr(vehicle, "vuna_vehicle_owner", None)
	if getattr(vehicle, "vuna_supplier", None):
		allocation.supplier = vehicle.vuna_supplier
	if len(loads) == 1:
		_validate_vehicle_category_for_load(loads[0], vehicle.vuna_vehicle_category)
	else:
		_validate_vehicle_category_for_loads(loads, vehicle.vuna_vehicle_category)
	_validate_vehicle_is_available(allocation, loads)
	lock_document("Driver", allocation.driver)
	_validate_driver_is_available(allocation, loads)


def _derive_vehicle_source(allocation):
	"""Derive allocation source and costing context from the selected Vehicle."""
	if not allocation.vehicle:
		return
	vehicle = frappe.get_doc("Vehicle", allocation.vehicle)
	owner = getattr(vehicle, "vuna_vehicle_owner", None)
	derived_type = "Subcontractor" if owner == "Supplier" else "Owned Fleet"
	allocation.allocation_type = derived_type
	allocation.vehicle_owner = owner or "Company"
	vehicle_category = getattr(vehicle, "vuna_vehicle_category", None)
	if allocation.required_vehicle_category and vehicle_category != allocation.required_vehicle_category:
		frappe.throw(
			_("Vehicle {0} belongs to category {1}, but this allocation requires {2}.").format(
				vehicle.name, vehicle_category or _("Not Set"), allocation.required_vehicle_category
			)
		)
	if getattr(vehicle, "vuna_supplier", None):
		allocation.supplier = vehicle.vuna_supplier
	vehicle_structure = getattr(vehicle, "vuna_transport_cost_structure", None)
	if allocation.cost_structure and allocation.cost_structure != vehicle_structure:
		frappe.throw(
			_("Transport Cost Structure {0} does not match Vehicle {1}'s configured structure {2}.").format(
				allocation.cost_structure, vehicle.name, vehicle_structure or _("Not Set")
			)
		)
	if vehicle_structure:
		allocation.cost_structure = vehicle_structure


def _validate_planning_window(load):
	if not load.planned_departure and not load.planned_arrival:
		return
	if not load.planned_departure or not load.planned_arrival:
		frappe.throw(
			_(
				"Set both Planned Departure and Planned Arrival on Delivery Load {0}, or leave both blank."
			).format(load.name)
		)
	if get_datetime(load.planned_departure) >= get_datetime(load.planned_arrival):
		frappe.throw(_("Planned Arrival must be after Planned Departure."))


def get_load_resource_window(load):
	"""Return the interval used for resource checks.

	When no explicit planning window exists, resource lookups represent current
	availability only. A future availability claim requires explicit times.
	"""
	if load.planned_departure and load.planned_arrival:
		return load.planned_departure, load.planned_arrival
	if load.planned_departure or load.planned_arrival:
		_validate_planning_window(load)
	now = now_datetime()
	return now, add_to_date(now, seconds=1)


def get_allocation_resource_window(loads):
	"""Use the complete selected-load period for Vehicle and Driver checks."""
	loads = list(loads or [])
	if not loads:
		now = now_datetime()
		return now, add_to_date(now, seconds=1)
	windows = [get_load_resource_window(load) for load in loads]
	return min(get_datetime(row[0]) for row in windows), max(get_datetime(row[1]) for row in windows)


def _prepare_allocation_load_rows(allocation, loads):
	"""Snapshot planning context onto the canonical child rows."""
	if len({load.name for load in loads}) != len(loads):
		frappe.throw(_("A Delivery Load can only be added once to a Vehicle Allocation."))
	for row, load in zip(allocation.delivery_loads, loads, strict=False):
		row.company = load.company
		row.required_vehicle_category = load.required_vehicle_category
		row.total_weight = load.total_weight
		row.weight_uom = load.weight_uom
		row.total_volume = load.total_volume
		row.volume_uom = load.volume_uom
		row.total_stops = load.total_stops
		groups = sorted(
			{
				item.transport_compatibility_group
				for item in load.delivery_orders
				if item.transport_compatibility_group
			}
		)
		row.transport_compatibility_groups = ", ".join(groups)


def _validate_selected_load_compatibility(loads):
	companies = {load.company for load in loads}
	if len(companies) != 1:
		frappe.throw(_("All Delivery Loads in one Vehicle Allocation must belong to the same Company."))
	categories = {load.required_vehicle_category for load in loads if load.required_vehicle_category}
	if len(categories) > 1:
		frappe.throw(_("Selected Delivery Loads must use the same Required Vehicle Category."))
	methods = {load.delivery_method for load in loads if load.delivery_method}
	if len(methods) > 1:
		frappe.throw(_("Selected Delivery Loads must use compatible Delivery Methods."))
	from vuna_transport.services.planning.load_validation_service import (
		validate_transport_compatibility_groups,
	)

	validate_transport_compatibility_groups(
		[row for load in loads for row in load.delivery_orders if row.delivery_order]
	)


def _build_combined_capacity_load(loads):
	weight_uoms = {load.weight_uom for load in loads if load.weight_uom}
	volume_uoms = {load.volume_uom for load in loads if load.volume_uom}
	if len(weight_uoms) > 1 or len(volume_uoms) > 1:
		frappe.throw(_("Selected Delivery Loads must use the same transport Weight and Volume UOMs."))
	body_types = {load.required_body_type for load in loads if load.required_body_type}
	if len(body_types) > 1:
		frappe.throw(_("Selected Delivery Loads require incompatible vehicle body types."))
	equipment = {
		value.strip()
		for load in loads
		for value in (load.special_equipment_required or "").replace("\n", ",").split(",")
		if value.strip()
	}
	return frappe._dict(
		name=", ".join(load.name for load in loads),
		weight_uom=next(iter(weight_uoms), None),
		volume_uom=next(iter(volume_uoms), None),
		total_weight=sum(flt(load.total_weight) for load in loads),
		total_volume=sum(flt(load.total_volume) for load in loads),
		total_pallets=sum(flt(load.total_pallets) for load in loads),
		total_cases=sum(flt(load.total_cases) for load in loads),
		total_crates=sum(flt(load.total_crates) for load in loads),
		refrigeration_required=any(load.refrigeration_required for load in loads),
		tail_lift_required=any(load.tail_lift_required for load in loads),
		required_body_type=next(iter(body_types), None),
		special_equipment_required=", ".join(sorted(equipment)),
		delivery_orders=[row for load in loads for row in load.delivery_orders],
	)


def _earliest_planned_departure(loads):
	departures = [get_datetime(load.planned_departure) for load in loads if load.planned_departure]
	return min(departures) if departures else None


def _append_load(allocation, load):
	allocation.append("delivery_loads", {"delivery_load": load.name})


def _coerce_loads(loads):
	return list(loads) if isinstance(loads, (list, tuple, set)) else [loads]


def _get_draft_allocation_for_load(load_name):
	rows = frappe.get_all(
		"Vehicle Allocation Load",
		filters={"delivery_load": load_name, "parenttype": "Vehicle Allocation"},
		fields=["parent"],
		order_by="creation desc",
	)
	for row in rows:
		if frappe.db.get_value("Vehicle Allocation", row.parent, "docstatus") == 0:
			return row.parent
	return None


def _validate_initial_allocation_sequence(load, allocation=None):
	"""Enforce the configured order for the first assignment, not replacements."""
	if allocation_is_required_before_delivery_notes():
		return
	if load.active_vehicle_allocation or (allocation and getattr(allocation, "replacement_of", None)):
		return
	if load.delivery_method and not frappe.db.get_value(
		"Delivery Method", load.delivery_method, "requires_vehicle_allocation"
	):
		return

	status = get_delivery_note_submission_status(load)
	if status["missing_delivery_orders"]:
		frappe.throw(
			_(
				"Generate Delivery Notes for every Delivery Order before allocating a vehicle. Missing: {0}."
			).format(", ".join(status["missing_delivery_orders"]))
		)
	if status["draft_delivery_notes"]:
		frappe.throw(
			_(
				"Submit all Delivery Notes for Delivery Load {0} before allocating a vehicle. Draft: {1}."
			).format(load.name, ", ".join(status["draft_delivery_notes"]))
		)


def get_delivery_note_submission_status(load):
	"""Return whether a load's active Delivery Notes are submitted and complete."""
	from vuna_transport.services.execution.lineage_service import get_delivery_notes_for_loads

	expected_orders = {row.delivery_order for row in load.delivery_orders if row.delivery_order}
	note_names = get_delivery_notes_for_loads([load.name])
	notes = frappe.get_all(
		"Delivery Note",
		filters={"name": ["in", note_names]},
		fields=["name", "docstatus", "vuna_delivery_order"],
	)
	active_notes = [note for note in notes if note.docstatus != 2]
	submitted_notes = [note for note in active_notes if note.docstatus == 1]
	draft_notes = [note for note in active_notes if note.docstatus == 0]
	submitted_orders = {note.vuna_delivery_order for note in submitted_notes if note.vuna_delivery_order}
	submitted_orders.update(
		frappe.get_all(
			"Delivery Load Order",
			filters={
				"name": [
					"in",
					frappe.get_all(
						"Delivery Note Item",
						filters={"parent": ["in", [note.name for note in submitted_notes]]},
						pluck="vuna_delivery_load_order",
					),
				]
			},
			fields=["delivery_order"],
			pluck="delivery_order",
		)
	)
	submitted_orders.discard(None)
	return {
		"expected_delivery_orders": sorted(expected_orders),
		"submitted_delivery_orders": sorted(expected_orders & submitted_orders),
		"missing_delivery_orders": sorted(expected_orders - submitted_orders),
		"draft_delivery_notes": sorted(note.name for note in draft_notes),
		"active_delivery_notes": sorted(note.name for note in active_notes),
		"submitted_delivery_notes": sorted(note.name for note in submitted_notes),
		"ready": not (expected_orders - submitted_orders) and not draft_notes,
	}


@frappe.whitelist()
def get_allocation_delivery_note_status(allocation_name):
	"""Return the one execution action currently valid for a Vehicle Allocation."""
	allocation = frappe.get_doc("Vehicle Allocation", allocation_name)
	require_document_permission(allocation, "read")
	return _get_allocation_execution_state(allocation)


def _get_allocation_execution_state(allocation):
	"""Return authoritative execution details for one allocation without permission checks."""
	active_delivery_trip = frappe.db.get_value(
		"Delivery Trip",
		{"vuna_current_vehicle_allocation": allocation.name, "docstatus": ["!=", 2]},
		"name",
	) or frappe.db.get_value(
		"Delivery Trip",
		{"vuna_vehicle_allocation": allocation.name, "docstatus": ["!=", 2]},
		"name",
	)
	loads = get_allocation_loads(allocation)
	statuses = [get_delivery_note_submission_status(load) for load in loads]
	trip = (
		frappe.db.get_value("Delivery Trip", active_delivery_trip, ["name", "docstatus", "status"], as_dict=True)
		if active_delivery_trip
		else None
	)
	return {
		"active_delivery_trip": active_delivery_trip,
		"ready_for_delivery_trip": not active_delivery_trip
		and bool(statuses)
		and all(status["ready"] for status in statuses),
		"draft_delivery_notes": sorted(
			{note for status in statuses for note in status["draft_delivery_notes"]}
		),
		"submitted_delivery_notes": sorted(
			{note for status in statuses for note in status["submitted_delivery_notes"]}
		),
		"execution_status": _get_allocation_execution_status(allocation, statuses, trip),
	}


def sync_allocation_execution_status(allocation):
	"""Persist the allocation lifecycle state so the standard list filter remains useful."""
	if isinstance(allocation, str):
		allocation = frappe.get_doc("Vehicle Allocation", allocation)
	if allocation.docstatus != 1 or allocation.status in ("Replaced", "Cancelled"):
		return
	status = _get_allocation_execution_state(allocation)["execution_status"]
	if status != allocation.status:
		allocation.db_set("status", status, update_modified=False)
		allocation.status = status


def sync_allocation_execution_status_for_delivery_note(doc, method=None):
	"""Refresh allocations affected by a Delivery Note submission or cancellation."""
	load_names = {getattr(doc, "vuna_delivery_load", None)}
	load_names.update(
		frappe.get_all(
			"Delivery Note Item",
			filters={"parent": doc.name},
			pluck="vuna_delivery_load",
		)
	)
	load_names.discard(None)
	if not load_names:
		return
	allocation_names = frappe.get_all(
		"Delivery Load",
		filters={"name": ["in", list(load_names)], "active_vehicle_allocation": ["is", "set"]},
		pluck="active_vehicle_allocation",
	)
	for allocation_name in set(allocation_names):
		sync_allocation_execution_status(allocation_name)


def sync_allocation_execution_status_for_delivery_trip(doc, method=None):
	"""Refresh the allocation lifecycle after a Delivery Trip changes state."""
	allocation_name = getattr(doc, "vuna_current_vehicle_allocation", None) or getattr(
		doc, "vuna_vehicle_allocation", None
	)
	if allocation_name:
		sync_allocation_execution_status(allocation_name)


def _get_allocation_execution_status(allocation, note_statuses, trip=None):
	"""Describe allocation progress from its authoritative execution documents."""
	docstatus = getattr(allocation, "docstatus", 1)
	allocation_status = getattr(allocation, "status", None) or "Allocated"
	if docstatus == 0:
		return "Draft"
	if docstatus == 2 or allocation_status == "Cancelled":
		return "Cancelled"
	if allocation_status == "Replaced":
		return "Replaced"
	if trip:
		if getattr(trip, "docstatus", None) == 0:
			return "Delivery Trip Draft"
		if getattr(trip, "status", None) == "In Transit":
			return "In Transit"
		if getattr(trip, "status", None) == "Completed":
			return "Completed"
		return "Delivery Trip Scheduled"
	if note_statuses and all(status["ready"] for status in note_statuses):
		return "Ready for Delivery Trip"
	if any(status.get("active_delivery_notes") for status in note_statuses):
		return "Awaiting Delivery Notes"
	return allocation_status


def _validate_vehicle_category_for_loads(loads, vehicle_category):
	if not vehicle_category:
		frappe.throw(_("Select a Vehicle Category for the allocated vehicle."))
	for load in loads:
		if load.required_vehicle_category and vehicle_category != load.required_vehicle_category:
			frappe.throw(
				_("Vehicle Category {0} does not match Delivery Load {1}'s required category {2}.").format(
					vehicle_category, load.name, load.required_vehicle_category
				)
			)
	from vuna_transport.services.fleet.vehicle_category_service import validate_minimum_load_requirements
	from vuna_transport.services.planning.delivery_load_service import validate_capacity

	combined = _build_combined_capacity_load(loads)
	category = frappe.get_doc("Vehicle Category", vehicle_category)
	validate_capacity(combined, category)
	validate_minimum_load_requirements(combined, category)


def _validate_vehicle_category_for_load(load, vehicle_category):
	"""Backward-compatible single-load validation entry point."""
	return _validate_vehicle_category_for_loads([load], vehicle_category)


def _validate_vehicle_is_available(allocation, loads):
	loads = _coerce_loads(loads)
	from_time, to_time = get_allocation_resource_window(loads)
	result = evaluate_vehicle_availability(
		allocation.vehicle,
		from_time,
		to_time,
		exclude_allocation=allocation.name,
	)
	if not result.dispatch_eligible:
		frappe.throw(_("Vehicle {0} is unavailable: {1}").format(allocation.vehicle, result.primary_reason))


def _validate_driver_is_available(allocation, loads):
	loads = _coerce_loads(loads)
	from_time, to_time = get_allocation_resource_window(loads)
	result = evaluate_driver_availability(
		allocation.driver,
		from_time,
		to_time,
		exclude_allocation=allocation.name,
		expected_company=loads[0].company,
	)
	if not result.dispatch_eligible:
		frappe.throw(_("Driver {0} is unavailable: {1}").format(allocation.driver, result.primary_reason))


def activate_allocation(allocation):
	loads = get_allocation_loads(allocation, lock=True)
	previous_names = {load.active_vehicle_allocation for load in loads if load.active_vehicle_allocation}
	if len(previous_names) > 1:
		frappe.throw(_("Selected Delivery Loads changed to different active Vehicle Allocations."))
	previous = next(iter(previous_names), None)
	if previous and previous != allocation.name:
		_lock_allocation(previous)
		if allocation.replacement_of != previous:
			frappe.throw(
				_(
					"The active allocation changed while this replacement was being prepared. Refresh and try again."
				)
			)
		previous_doc = frappe.get_doc("Vehicle Allocation", previous)
		full_replacement = set(get_allocation_load_names(previous_doc)) == {load.name for load in loads}
		if full_replacement:
			effective_from = allocation.effective_from or now_datetime()
			frappe.db.set_value(
				"Vehicle Allocation",
				previous,
				{
					"status": "Replaced",
					"effective_to": effective_from,
					"replaced_by": allocation.name,
				},
				update_modified=False,
			)
		else:
			previous_doc.add_comment(
				"Info",
				_(
					"Delivery Loads {0} were reallocated to {1}; the original allocation remains active for its other loads."
				).format(", ".join(sorted(load.name for load in loads)), allocation.name),
			)
	for load in loads:
		if previous:
			load.add_comment(
				"Info",
				_("Vehicle Allocation {0} replaced {1}. Reason: {2}. Details: {3}").format(
					allocation.name, previous, allocation.replacement_reason, allocation.replacement_details
				),
			)
		load.db_set("active_vehicle_allocation", allocation.name, update_modified=False)
		_sync_trip_execution_allocation(load, allocation)
		if load.status in ("Approved", "Proposed", "Under Review", "Awaiting Vehicle"):
			has_delivery_notes = frappe.db.exists(
				"Delivery Note", {"vuna_delivery_load": load.name, "docstatus": ["!=", 2]}
			)
			target_status = "Ready for Dispatch" if has_delivery_notes else "Allocated"
			set_delivery_load_status(
				load, target_status, _("Vehicle Allocation {0} submitted").format(allocation.name)
			)


def prepare_allocation_cancellation(allocation):
	"""Detach the active allocation before Frappe checks linked-document cancellation."""
	for load in get_allocation_loads(allocation, lock=True):
		if load.active_vehicle_allocation == allocation.name:
			load.db_set("active_vehicle_allocation", None, update_modified=True)


def restore_previous_allocation(allocation, active_link_cleared=False):
	loads = get_allocation_loads(allocation, lock=True)
	if not active_link_cleared and any(load.active_vehicle_allocation != allocation.name for load in loads):
		return
	previous_name = allocation.replacement_of
	if previous_name:
		previous = frappe.get_doc("Vehicle Allocation", previous_name)
		if set(get_allocation_load_names(previous)) == set(get_allocation_load_names(allocation)):
			frappe.db.set_value(
				"Vehicle Allocation",
				previous_name,
				{"status": "Allocated", "effective_to": None, "replaced_by": None},
				update_modified=False,
			)
	for load in loads:
		load.db_set("active_vehicle_allocation", previous_name, update_modified=False)
		if previous_name:
			_sync_trip_execution_allocation(
				load, frappe.get_doc("Vehicle Allocation", previous_name), rollback=True
			)
		if not previous_name:
			has_delivery_notes = frappe.db.exists(
				"Delivery Note", {"vuna_delivery_load": load.name, "docstatus": ["!=", 2]}
			)
			target_status = "Awaiting Vehicle" if has_delivery_notes else "Approved"
			if load.status not in ("Completed", "Completed with Exceptions", "Dispatched"):
				set_delivery_load_status(
					load, target_status, _("Vehicle Allocation {0} cancelled").format(allocation.name)
				)


def validate_allocation_cancellation(allocation):
	"""Protect submitted history and prevent restoring a failed vehicle in transit."""
	if allocation.replaced_by:
		frappe.throw(
			_("Vehicle Allocation {0} is historical and was replaced by {1}; it cannot be cancelled.").format(
				allocation.name, allocation.replaced_by
			)
		)
	loads = get_allocation_loads(allocation)
	if any(load.active_vehicle_allocation != allocation.name for load in loads):
		frappe.throw(_("Only the active Vehicle Allocation can be cancelled."))
	trips = {_get_active_trip(load).name for load in loads if _get_active_trip(load)}
	if any(frappe.db.get_value("Delivery Trip", trip, "docstatus") == 1 for trip in trips):
		frappe.throw(
			_(
				"An allocation used by an active trip cannot be cancelled. Create another reallocation instead."
			)
		)


def _get_active_trip(load):
	name = load.delivery_trip or frappe.db.get_value(
		"Delivery Stop",
		{
			"vuna_delivery_load": load.name,
			"parenttype": "Delivery Trip",
			"docstatus": ["!=", 2],
		},
		"parent",
	)
	if not name:
		return None
	return frappe.db.get_value("Delivery Trip", name, ["name", "docstatus", "status"], as_dict=True)


def _validate_trip_allows_reallocation(trip):
	if trip and (trip.docstatus == 2 or trip.status in ("Completed", "Cancelled")):
		frappe.throw(
			_("Vehicle reallocation is not allowed after the Delivery Trip is completed or cancelled.")
		)


def _get_replacement_stage(trip):
	return "In Transit" if trip and trip.status == "In Transit" else "Before Departure"


def _sync_trip_execution_allocation(load, allocation, rollback=False):
	trip = _get_active_trip(load)
	if not trip:
		return
	values = {
		"vuna_current_vehicle_allocation": allocation.name,
		"vuna_current_vehicle": allocation.vehicle,
		"vuna_current_driver": allocation.driver,
	}
	if trip.docstatus == 0 or trip.status == "Scheduled":
		values.update(
			{
				"vehicle": allocation.vehicle,
				"driver": allocation.driver,
				"vuna_vehicle_allocation": allocation.name,
			}
		)
	current_count = frappe.db.get_value("Delivery Trip", trip.name, "vuna_reallocation_count") or 0
	if allocation.assignment_event == "Reallocation" or rollback:
		values["vuna_reallocation_count"] = max(current_count + (-1 if rollback else 1), 0)
	frappe.db.set_value("Delivery Trip", trip.name, values, update_modified=True)
	frappe.get_doc("Delivery Trip", trip.name).add_comment(
		"Info",
		_("Execution allocation changed to {0}: vehicle {1}, driver {2}.").format(
			allocation.name,
			allocation.vehicle,
			allocation.driver,
		),
	)


def _lock_allocation(name):
	frappe.db.sql("select name from `tabVehicle Allocation` where name = %s for update", name)


def get_active_allocation(load, required=False):
	load = frappe.get_doc("Delivery Load", load) if isinstance(load, str) else load
	name = load.active_vehicle_allocation
	if name:
		allocation = frappe.get_doc("Vehicle Allocation", name)
		if allocation.docstatus == 1 and allocation.status in ACTIVE_STATUSES:
			return allocation
	if required:
		frappe.throw(_("Allocate a vehicle and driver to Delivery Load {0} first.").format(load.name))
	return None
