"""Execution progress and next-action state for Transport Loads."""

import frappe
from frappe import _

from vuna_transport.services.fleet.vehicle_allocation_service import (
	get_allocation_names_for_load,
	get_delivery_note_submission_status,
)
from vuna_transport.services.shared.security_service import require_document_permission
from vuna_transport.services.shared.workflow_policy_service import (
	ALLOCATION_AFTER_DELIVERY_NOTES,
	get_vehicle_allocation_timing,
)

DOCSTATUS_LABELS = {0: "Draft", 1: "Submitted", 2: "Cancelled"}


@frappe.whitelist()
def get_load_execution_summary(load):
	"""Return downstream execution progress and the single recommended next action."""
	load = frappe.get_doc("Transport Load", load) if isinstance(load, str) else load
	require_document_permission(load, "read")
	from vuna_transport.services.planning.transport_load_service import is_demand_load

	if is_demand_load(load):
		return {"planning_only": True, "load": {"docstatus": load.docstatus, "status": load.status}}
	delivery_orders = {row.delivery_order for row in load.delivery_orders if row.delivery_order}

	pick_list = _get_pick_list(load)
	vehicle_allocation = _get_vehicle_allocation(load)
	allocation_names = get_allocation_names_for_load(load.name)
	vehicle_allocation_history = frappe.get_all(
		"Vehicle Allocation",
		filters={"name": ["in", allocation_names or [""]]},
		fields=[
			"name",
			"docstatus",
			"status",
			"assignment_event",
			"vehicle",
			"driver",
			"replacement_of",
			"replacement_reason",
			"replacement_stage",
			"effective_from",
			"effective_to",
		],
		order_by="effective_from asc, creation asc",
	)
	delivery_notes = frappe.get_all(
		"Delivery Note",
		filters={"vuna_delivery_load": load.name},
		fields=["name", "docstatus", "status", "customer", "vuna_delivery_order"],
		order_by="creation asc",
	)
	active_notes = [note for note in delivery_notes if note.docstatus != 2]
	submitted_notes = [note for note in active_notes if note.docstatus == 1]
	draft_notes = [note for note in active_notes if note.docstatus == 0]
	delivery_note_status = get_delivery_note_submission_status(load)

	delivery_trip = _get_delivery_trip(load)
	stops = []
	if delivery_trip:
		stops = frappe.get_all(
			"Delivery Stop",
			filters={"parent": delivery_trip.name, "parenttype": "Delivery Trip"},
			fields=[
				"name",
				"customer",
				"delivery_note",
				"visited",
				"vuna_delivery_status",
				"vuna_proof_of_delivery",
			],
			order_by="idx asc",
		)

	pods = frappe.get_all(
		"Proof of Delivery",
		filters={"delivery_load": load.name, "docstatus": ["!=", 2]},
		fields=["name", "docstatus", "status", "delivery_stop"],
		order_by="creation asc",
	)
	submitted_pods = [pod for pod in pods if pod.docstatus == 1]
	return_requests = (
		frappe.get_all(
			"Return Request",
			filters={"delivery_trip": delivery_trip.name} if delivery_trip else {"name": ""},
			fields=["name", "status", "proof_of_delivery"],
			order_by="creation asc",
		)
		if delivery_trip
		else []
	)
	vehicle_allocation_timing = get_vehicle_allocation_timing()

	next_action = _get_next_action(
		load,
		vehicle_allocation,
		pick_list,
		delivery_orders,
		active_notes,
		submitted_notes,
		draft_notes,
		delivery_trip,
		stops,
		submitted_pods,
		[],
		vehicle_allocation_timing,
		delivery_note_status["ready"],
	)

	return {
		"load": {"docstatus": load.docstatus, "status": load.status},
		"workflow": {
			"requires_vehicle_allocation": _requires_vehicle_allocation(load),
			"requires_delivery_trip": _requires_delivery_trip(load),
			"vehicle_allocation_timing": vehicle_allocation_timing,
		},
		"delivery_orders": {"total": len(delivery_orders)},
		"vehicle_allocation": _serialize_doc(
			vehicle_allocation,
			("allocation_type", "vehicle", "driver", "supplier"),
		),
		"vehicle_allocation_history": vehicle_allocation_history,
		"pick_list": _serialize_doc(pick_list),
		"delivery_notes": {
			"expected": len(delivery_orders),
			"total": len(active_notes),
			"draft": len(draft_notes),
			"submitted": len(submitted_notes),
			"cancelled": len([note for note in delivery_notes if note.docstatus == 2]),
			"names": [note.name for note in active_notes],
			"ready_for_vehicle_allocation": delivery_note_status["ready"],
			"missing_delivery_orders": delivery_note_status["missing_delivery_orders"],
			"draft_names": delivery_note_status["draft_delivery_notes"],
		},
		"delivery_trip": _serialize_doc(delivery_trip, ("vehicle", "driver")),
		"stops": {
			"total": len(stops),
			"visited": len([stop for stop in stops if stop.visited]),
			"pending": len([stop for stop in stops if not stop.visited]),
		},
		"proofs_of_delivery": {
			"total": len(pods),
			"submitted": len(submitted_pods),
			"draft": len([pod for pod in pods if pod.docstatus == 0]),
		},
		"return_requests": {"total": len(return_requests), "names": [row.name for row in return_requests]},
		"next_action": next_action,
	}


def _get_pick_list(load):
	if not load.pick_list:
		return None
	value = frappe.db.get_value("Pick List", load.pick_list, ["name", "docstatus", "status"], as_dict=True)
	return value


def _get_vehicle_allocation(load):
	if load.active_vehicle_allocation:
		return frappe.db.get_value(
			"Vehicle Allocation",
			load.active_vehicle_allocation,
			["name", "docstatus", "status", "allocation_type", "vehicle", "driver", "supplier"],
			as_dict=True,
		)
	draft_names = get_allocation_names_for_load(load.name, docstatus=0)
	name = draft_names[-1] if draft_names else None
	if not name:
		return None
	return frappe.db.get_value(
		"Vehicle Allocation",
		name,
		["name", "docstatus", "status", "allocation_type", "vehicle", "driver", "supplier"],
		as_dict=True,
	)


def _get_delivery_trip(load):
	name = load.delivery_trip or frappe.db.get_value(
		"Delivery Stop",
		{"vuna_delivery_load": load.name, "docstatus": ["!=", 2]},
		"parent",
	)
	if not name:
		return None
	return frappe.db.get_value(
		"Delivery Trip",
		name,
		[
			"name",
			"docstatus",
			"status",
			"vehicle",
			"driver",
			"vuna_current_vehicle",
			"vuna_current_driver",
			"vuna_reallocation_count",
		],
		as_dict=True,
	)


def _serialize_doc(doc, extra_fields=()):
	if not doc:
		return None
	result = {
		"name": doc.name,
		"docstatus": doc.docstatus,
		"document_status": DOCSTATUS_LABELS.get(doc.docstatus),
		"status": doc.status,
	}
	for field in extra_fields:
		result[field] = doc.get(field)
	return result


def _get_next_action(
	load,
	vehicle_allocation,
	pick_list,
	delivery_orders,
	active_notes,
	submitted_notes,
	draft_notes,
	delivery_trip,
	stops,
	submitted_pods,
	open_exceptions,
	vehicle_allocation_timing,
	delivery_notes_ready=None,
):
	if delivery_notes_ready is None:
		submitted_orders = {
			note.vuna_delivery_order for note in submitted_notes if getattr(note, "vuna_delivery_order", None)
		}
		delivery_notes_ready = not draft_notes and delivery_orders.issubset(submitted_orders)
	if load.docstatus == 0:
		return _action("submit_load", _("Submit Transport Load"), _("Review capacity and submit this load."))
	if load.docstatus == 2:
		return _action("cancelled", _("Transport Load Cancelled"), _("Amend the load to continue."))
	if load.status in ("Completed", "Completed with Exceptions"):
		return _action("completed", _("Execution Completed"), _("This Transport Load is closed."))
	requires_vehicle_allocation = _requires_vehicle_allocation(load)
	allocation_after_notes = vehicle_allocation_timing == ALLOCATION_AFTER_DELIVERY_NOTES
	if requires_vehicle_allocation and not allocation_after_notes:
		action = _get_vehicle_allocation_action(load, vehicle_allocation)
		if action:
			return action
	if pick_list and pick_list.docstatus == 0:
		return _action(
			"open_pick_list", _("Complete Pick List"), _("Pick and submit the warehouse document.")
		)
	if requires_vehicle_allocation and allocation_after_notes:
		if draft_notes:
			return _action(
				"review_delivery_notes",
				_("Review Delivery Notes"),
				_("Submit every Delivery Note before allocating a vehicle."),
			)
		if not delivery_notes_ready:
			return _action(
				"create_delivery_notes",
				_("Create Delivery Notes"),
				_("Create and submit Delivery Notes before allocating a vehicle."),
			)
		action = _get_vehicle_allocation_action(load, vehicle_allocation)
		if action:
			return action
	if len(active_notes) < len(delivery_orders):
		return _action(
			"create_delivery_notes",
			_("Create Delivery Notes"),
			_("Create one stock movement per delivery stop."),
		)
	if draft_notes:
		return _action(
			"review_delivery_notes", _("Review Delivery Notes"), _("Review and submit every Delivery Note.")
		)
	if len(submitted_notes) < len(delivery_orders):
		return _action(
			"create_delivery_notes",
			_("Replace Missing Delivery Notes"),
			_("Create Delivery Notes for unresolved Delivery Orders."),
		)
	if not _requires_delivery_trip(load):
		return _action(
			"completed",
			_("Fulfilment Completed"),
			_("This Delivery Method does not require a Delivery Trip."),
		)
	if not delivery_trip or delivery_trip.docstatus == 2:
		return _action(
			"create_delivery_trip", _("Create Delivery Trip"), _("Assign the actual vehicle and driver.")
		)
	if delivery_trip.docstatus == 0:
		return _action(
			"open_delivery_trip",
			_("Complete Delivery Trip"),
			_("Assign vehicle and driver, then submit the trip."),
		)
	if not stops:
		return _action(
			"open_delivery_trip", _("Review Delivery Trip"), _("The submitted trip has no delivery stops.")
		)
	if len(submitted_pods) < len(stops):
		return _action(
			"open_delivery_trip",
			_("Capture Proofs of Delivery"),
			_("Record the outcome for every delivery stop."),
		)
	return _action(
		"completed", _("Execution Completed"), _("Every delivery stop has a submitted Proof of Delivery.")
	)


def _get_vehicle_allocation_action(load, vehicle_allocation):
	if not vehicle_allocation or vehicle_allocation.docstatus == 2:
		return _action(
			"create_vehicle_allocation",
			_("Allocate Vehicle"),
			_("Assign the physical vehicle and driver."),
		)
	if vehicle_allocation.docstatus == 0:
		return _action(
			"open_vehicle_allocation",
			_("Complete Vehicle Allocation"),
			_("Review and submit the allocation."),
		)
	return None


def _requires_vehicle_allocation(load):
	if not load.delivery_method:
		return True
	return bool(frappe.db.get_value("Delivery Method", load.delivery_method, "requires_vehicle_allocation"))


def _requires_delivery_trip(load):
	if not load.delivery_method:
		return True
	return bool(frappe.db.get_value("Delivery Method", load.delivery_method, "requires_delivery_trip"))


def _action(key, label, description):
	return {"key": key, "label": label, "description": description}
