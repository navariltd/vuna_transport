"""Services for creating and applying proof of delivery outcomes."""

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from vuna_transport.services.execution.dispatch_guard_service import set_delivery_load_status
from vuna_transport.services.execution.lineage_service import get_delivery_trip_load_names
from vuna_transport.services.execution.stock_identity_service import (
	get_single_delivery_note_item_batch,
)
from vuna_transport.services.shared.security_service import (
	require_doctype_permission,
	require_document_permission,
)


@frappe.whitelist()
def create_proof_of_delivery(delivery_trip, delivery_stop):
	"""Create or repair a draft POD from an ERPNext Delivery Stop."""
	require_doctype_permission("Proof of Delivery", "create")
	trip = frappe.get_doc("Delivery Trip", delivery_trip)
	require_document_permission(trip, "read")
	if trip.docstatus != 1:
		frappe.throw(_("Submit the Delivery Trip before creating a Proof of Delivery."))
	if not get_delivery_trip_load_names(trip):
		frappe.throw(_("Delivery Trip is not linked to a Vehicle Allocation or Delivery Load."))

	stop = next((row for row in trip.delivery_stops if row.name == delivery_stop), None)
	if not stop:
		frappe.throw(_("The selected Delivery Stop does not belong to this Delivery Trip."))
	if not stop.delivery_note:
		frappe.throw(_("The Delivery Stop must reference a Delivery Note."))

	delivery_note = frappe.get_doc("Delivery Note", stop.delivery_note)
	if delivery_note.docstatus != 1:
		frappe.throw(_("Submit Delivery Note {0} first.").format(delivery_note.name))

	existing = stop.vuna_proof_of_delivery or frappe.db.get_value(
		"Proof of Delivery",
		{"delivery_trip": trip.name, "delivery_stop": stop.name, "docstatus": ["!=", 2]},
	)
	pod = frappe.get_doc("Proof of Delivery", existing) if existing else frappe.new_doc("Proof of Delivery")
	pod.company = trip.company
	pod.delivery_trip = trip.name
	pod.delivery_stop = stop.name
	pod.delivery_load = delivery_note.vuna_delivery_load
	pod.delivery_order = stop.vuna_delivery_order or delivery_note.vuna_delivery_order
	pod.delivery_note = delivery_note.name
	pod.customer = stop.customer
	pod.delivery_address = stop.address
	pod.delivery_datetime = pod.delivery_datetime or now_datetime()

	rebuild = not pod.items or any(
		not row.delivery_note_item or flt(row.ordered_qty) <= 0 or not row.uom for row in pod.items
	)
	if rebuild:
		pod.set("items", [])
		for delivery_item in delivery_note.items:
			delivery_order_item = frappe.db.get_value(
				"Delivery Order Item",
				{
					"parent": pod.delivery_order,
					"sales_order_item": delivery_item.so_detail,
				},
			)
			ordered_qty = flt(delivery_item.qty)
			pod.append(
				"items",
				{
					"item_code": delivery_item.item_code,
					"item_name": delivery_item.item_name,
					"delivery_order_item": delivery_order_item,
					"delivery_note_item": delivery_item.name,
					"ordered_qty": ordered_qty,
					"delivered_qty": ordered_qty,
					"rejected_qty": 0,
					"uom": delivery_item.uom,
					"stock_uom": delivery_item.stock_uom,
					"conversion_factor": flt(delivery_item.conversion_factor) or 1,
					"batch_no": get_single_delivery_note_item_batch(delivery_item),
				},
			)

	if not pod.items:
		frappe.throw(_("Delivery Note {0} has no items.").format(delivery_note.name))
	pod.flags.ignore_mandatory = True
	if pod.is_new():
		pod.insert()
	else:
		pod.save()
	frappe.db.set_value("Delivery Stop", stop.name, "vuna_proof_of_delivery", pod.name, update_modified=False)
	return pod.name


def derive_pod_status(items):
	total_ordered = sum(flt(item.ordered_qty) for item in items)
	total_delivered = sum(flt(item.delivered_qty) for item in items)
	total_rejected = sum(flt(item.rejected_qty) for item in items)
	if total_delivered <= 0 and total_rejected > 0:
		return "Rejected"
	if total_delivered <= 0:
		return "Failed"
	if total_rejected > 0 or total_delivered < total_ordered:
		return "Partially Delivered"
	return "Accepted"


def validate_pod_items(items, rejection_reasons=None):
	rejection_reasons = rejection_reasons or []
	if not items:
		frappe.throw(_("At least one Proof of Delivery item is required."))
	for item in items:
		item.rejected_qty = max(flt(item.ordered_qty) - flt(item.delivered_qty), 0)
		if flt(item.delivered_qty) < 0 or flt(item.rejected_qty) < 0:
			frappe.throw(_("Delivered and rejected quantities cannot be negative."))
		if flt(item.delivered_qty) + flt(item.rejected_qty) > flt(item.ordered_qty):
			frappe.throw(
				_("Delivered and rejected quantities cannot exceed ordered quantity for item {0}.").format(
					item.item_code
				)
			)
		item_reasons = list(rejection_reasons)
		if getattr(item, "rejection_reason", None):
			item_reasons.append(item.rejection_reason)
		# Preserve compatibility with older in-memory/test documents while existing
		# records transition away from the unsupported nested Table MultiSelect.
		item_reasons.extend(getattr(item, "rejection_reasons", None) or [])
		if (
			flt(item.rejected_qty)
			and not item_reasons
			and not getattr(item, "rejection_reason_remarks", None)
		):
			frappe.throw(_("A rejection reason is required for item {0}.").format(item.item_code))


def update_delivery_stop_from_pod(pod, status):
	existing_arrival = frappe.db.get_value("Delivery Stop", pod.delivery_stop, "vuna_actual_arrival")
	frappe.db.set_value(
		"Delivery Stop",
		pod.delivery_stop,
		{
			"visited": 1,
			"vuna_delivery_status": status,
			"vuna_actual_arrival": existing_arrival or pod.delivery_datetime,
			"vuna_actual_departure": pod.delivery_datetime,
			"vuna_proof_of_delivery": pod.name,
		},
		update_modified=False,
	)
	trip = frappe.get_doc("Delivery Trip", pod.delivery_trip)
	trip.update_status()
	if trip.vuna_actual_start and _all_delivery_trip_stops_visited(trip.name):
		trip.db_set(
			{
				"vuna_actual_completion": pod.delivery_datetime,
				"status": "Completed",
			},
			update_modified=False,
		)
	elif trip.vuna_actual_start and trip.status == "Scheduled":
		trip.db_set("status", "In Transit", update_modified=False)
	recompute_delivery_load_completion(pod.delivery_load, pod.delivery_trip)
	if pod.delivery_order:
		recompute_delivery_order_status(pod.delivery_order)


def reset_delivery_stop_from_pod(pod):
	frappe.db.set_value(
		"Delivery Stop",
		pod.delivery_stop,
		{
			"visited": 0,
			"vuna_delivery_status": "Pending",
			"vuna_actual_departure": None,
			"vuna_proof_of_delivery": None,
		},
		update_modified=False,
	)
	trip = frappe.get_doc("Delivery Trip", pod.delivery_trip)
	if trip.vuna_actual_completion:
		trip.db_set("vuna_actual_completion", None, update_modified=False)
	trip.update_status()
	if trip.vuna_actual_start and trip.status == "Scheduled":
		trip.db_set("status", "In Transit", update_modified=False)
	set_delivery_load_status(
		pod.delivery_load,
		"Dispatched",
		_("Proof of Delivery {0} cancelled").format(pod.name),
	)
	if pod.delivery_order:
		recompute_delivery_order_status(pod.delivery_order)


def prepare_pod_cancellation(pod):
	"""Allow a POD correction without requiring its parent trip to be cancelled first."""
	# An active return case depends on this POD as its source evidence. Keep that
	# case intact and require it to be cancelled before the source POD changes.
	if frappe.db.table_exists("Return Request"):
		active_return = frappe.db.get_value(
			"Return Request",
			{"proof_of_delivery": pod.name, "docstatus": ["!=", 2]},
			"name",
		)
		if active_return:
			frappe.throw(
				_("Cancel or close Return Request {0} before cancelling Proof of Delivery {1}.").format(
					active_return, pod.name
				)
			)

	# Delivery Stop is a child of the submitted Delivery Trip and stores the
	# POD back-reference. reset_delivery_stop_from_pod clears that reference on
	# cancel; this flag prevents Frappe's generic back-link check from creating
	# a parent/child cancellation cycle.
	pod.ignore_linked_doctypes = ["Delivery Trip"]


def clear_pod_stop_reference(pod):
	"""Remove the draft POD backlink before Frappe checks links on deletion."""
	if not pod.delivery_stop or not frappe.db.exists("Delivery Stop", pod.delivery_stop):
		return
	if frappe.db.get_value("Delivery Stop", pod.delivery_stop, "vuna_proof_of_delivery") == pod.name:
		frappe.db.set_value(
			"Delivery Stop",
			pod.delivery_stop,
			"vuna_proof_of_delivery",
			None,
			update_modified=False,
		)


def recompute_delivery_order_status(delivery_order_name):
	"""Derive one Delivery Order status across all partial loads and stop outcomes."""
	delivery_order = frappe.db.get_value(
		"Delivery Order",
		delivery_order_name,
		["requested_quantity", "allocated_quantity", "reserved_quantity"],
		as_dict=True,
	)
	if not delivery_order:
		return

	loads = frappe.db.sql(
		"""
			select distinct dl.name, dl.status
			from `tabDelivery Load` dl
			join `tabDelivery Load Order` dlo on dlo.parent = dl.name
			where dl.docstatus = 1
			and dlo.parenttype = 'Delivery Load'
			and dlo.delivery_order = %s
		""",
		delivery_order_name,
		as_dict=True,
	)
	pods = frappe.get_all(
		"Proof of Delivery",
		filters={"delivery_order": delivery_order_name, "docstatus": 1},
		fields=["delivery_load", "status"],
	)
	status = derive_delivery_order_execution_status(
		requested_quantity=delivery_order.requested_quantity,
		allocated_quantity=delivery_order.allocated_quantity,
		reserved_quantity=delivery_order.reserved_quantity,
		loads=loads,
		pod_outcomes=pods,
	)
	frappe.db.set_value("Delivery Order", delivery_order_name, "status", status, update_modified=False)


def derive_delivery_order_execution_status(
	requested_quantity,
	allocated_quantity,
	reserved_quantity,
	loads,
	pod_outcomes,
):
	"""Pure status rule supporting one Delivery Order split across many loads."""
	load_statuses = {load.name: load.status for load in loads}
	outcomes = {pod.delivery_load: pod.status for pod in pod_outcomes if pod.delivery_load}
	for load_name, load_status in load_statuses.items():
		if load_status == "Completed":
			outcomes.setdefault(load_name, "Accepted")
		elif load_status == "Completed with Exceptions":
			outcomes.setdefault(load_name, "Partially Delivered")

	active_loads = set(load_statuses)
	all_covered = bool(active_loads) and active_loads.issubset(outcomes)
	fully_allocated = (
		flt(allocated_quantity) >= flt(requested_quantity)
		and not flt(reserved_quantity)
		and flt(requested_quantity) > 0
	)
	positive = any(status in ("Accepted", "Partially Delivered") for status in outcomes.values())
	exceptions = any(status in ("Rejected", "Failed") for status in outcomes.values())
	if all_covered and fully_allocated:
		if outcomes and all(status == "Accepted" for status in outcomes.values()):
			return "Delivered"
		if positive:
			return "Partially Delivered"
		if exceptions:
			return "Partially Delivered"
	if positive:
		return "Partially Delivered"
	if exceptions:
		return "Partially Delivered"
	if any(
		status in ("Dispatched", "Completed", "Completed with Exceptions")
		for status in load_statuses.values()
	):
		return "Dispatched"
	if flt(allocated_quantity) >= flt(requested_quantity) and flt(requested_quantity) > 0:
		return "Allocated"
	if flt(allocated_quantity):
		return "Partially Allocated"
	return "Open"


def recompute_delivery_load_completion(delivery_load, delivery_trip=None):
	"""Complete a load when every stop has a submitted POD.

	Return Requests are follow-up cases and do not keep the trip or vehicle
	blocked after the delivery event has been recorded.
	"""
	if not delivery_load or not frappe.db.exists("Delivery Load", delivery_load):
		return
	trip_name = delivery_trip or frappe.db.get_value(
		"Delivery Stop",
		{"vuna_delivery_load": delivery_load, "parenttype": "Delivery Trip", "docstatus": ["!=", 2]},
		"parent",
	)
	if not trip_name:
		return
	stops = frappe.get_all(
		"Delivery Stop",
		filters={"parent": trip_name, "parenttype": "Delivery Trip", "vuna_delivery_load": delivery_load},
		fields=["name", "visited"],
	)
	if not stops or not all(stop.visited for stop in stops):
		return
	pod_statuses = frappe.get_all(
		"Proof of Delivery",
		filters={"delivery_load": delivery_load, "delivery_trip": trip_name, "docstatus": 1},
		pluck="status",
	)
	if len(pod_statuses) < len(stops):
		return
	from vuna_transport.services.execution.dispatch_guard_service import set_delivery_load_status

	target = (
		"Completed"
		if pod_statuses and all(status == "Accepted" for status in pod_statuses)
		else "Completed with Exceptions"
	)
	set_delivery_load_status(
		delivery_load,
		target,
		_("Every delivery stop has a submitted Proof of Delivery."),
	)


def _all_delivery_trip_stops_visited(delivery_trip):
	stops = frappe.get_all(
		"Delivery Stop",
		filters={"parent": delivery_trip, "parenttype": "Delivery Trip"},
		pluck="visited",
	)
	return bool(stops) and all(cint(visited) for visited in stops)
