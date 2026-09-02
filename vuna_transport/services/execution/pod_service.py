"""Services for creating and applying proof of delivery outcomes."""

import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from vuna_transport.services.execution.dispatch_guard_service import set_delivery_load_status
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
	if not trip.vuna_delivery_load:
		frappe.throw(_("Delivery Trip is not linked to a Delivery Load."))

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
	pod.delivery_load = trip.vuna_delivery_load
	pod.delivery_order = (
		stop.vuna_delivery_order or delivery_note.vuna_delivery_order
	)
	pod.delivery_note = delivery_note.name
	pod.customer = stop.customer
	pod.delivery_address = stop.address
	pod.delivery_datetime = pod.delivery_datetime or now_datetime()

	rebuild = not pod.items or any(
		not row.delivery_note_item or flt(row.ordered_qty) <= 0 for row in pod.items
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
			ordered_qty = flt(delivery_item.stock_qty or delivery_item.qty)
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
					"batch_no": getattr(delivery_item, "batch_no", None),
				},
			)

	if not pod.items:
		frappe.throw(_("Delivery Note {0} has no items.").format(delivery_note.name))
	pod.flags.ignore_mandatory = True
	if pod.is_new():
		pod.insert()
	else:
		pod.save()
	frappe.db.set_value(
		"Delivery Stop", stop.name, "vuna_proof_of_delivery", pod.name, update_modified=False
	)
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


def validate_pod_items(items):
	if not items:
		frappe.throw(_("At least one Proof of Delivery item is required."))
	for item in items:
		if flt(item.delivered_qty) < 0 or flt(item.rejected_qty) < 0:
			frappe.throw(_("Delivered and rejected quantities cannot be negative."))
		if flt(item.delivered_qty) + flt(item.rejected_qty) > flt(item.ordered_qty):
			frappe.throw(
				_("Delivered and rejected quantities cannot exceed ordered quantity for item {0}.").format(
					item.item_code
				)
			)
		if flt(item.rejected_qty) and not item.rejection_reason:
			frappe.throw(_("A rejection reason is required for item {0}.").format(item.item_code))


def update_delivery_stop_from_pod(pod, status):
	frappe.db.set_value(
		"Delivery Stop",
		pod.delivery_stop,
		{
			"visited": 1,
			"vuna_delivery_status": status,
			"vuna_actual_arrival": pod.delivery_datetime,
			"vuna_actual_departure": pod.delivery_datetime,
			"vuna_proof_of_delivery": pod.name,
		},
		update_modified=False,
	)
	trip = frappe.get_doc("Delivery Trip", pod.delivery_trip)
	trip.update_status()
	from vuna_transport.services.execution.delivery_exception_service import (
		recompute_delivery_load_completion,
	)

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
			"vuna_actual_arrival": None,
			"vuna_actual_departure": None,
			"vuna_proof_of_delivery": None,
		},
		update_modified=False,
	)
	frappe.get_doc("Delivery Trip", pod.delivery_trip).update_status()
	set_delivery_load_status(
		pod.delivery_load,
		"Dispatched",
		_("Proof of Delivery {0} cancelled").format(pod.name),
	)
	if pod.delivery_order:
		recompute_delivery_order_status(pod.delivery_order)


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
	from vuna_transport.services.execution.delivery_exception_service import (
		get_delivery_order_exception_outcomes,
	)

	exception_outcomes = get_delivery_order_exception_outcomes(delivery_order_name)
	status = derive_delivery_order_execution_status(
		requested_quantity=delivery_order.requested_quantity,
		allocated_quantity=delivery_order.allocated_quantity,
		reserved_quantity=delivery_order.reserved_quantity,
		loads=loads,
		pod_outcomes=pods,
		delivery_exceptions=exception_outcomes,
	)
	frappe.db.set_value(
		"Delivery Order", delivery_order_name, "status", status, update_modified=False
	)


def derive_delivery_order_execution_status(
	requested_quantity,
	allocated_quantity,
	reserved_quantity,
	loads,
	pod_outcomes,
	delivery_exceptions=None,
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
	delivery_exceptions = delivery_exceptions or []
	all_exceptions_resolved = bool(delivery_exceptions) and all(
		exception.resolution_status in ("Resolved", "Closed")
		for exception in delivery_exceptions
	)

	if all_covered and fully_allocated:
		if outcomes and all(status == "Accepted" for status in outcomes.values()):
			return "Delivered"
		if all_exceptions_resolved:
			return "Resolved with Exceptions"
		if positive:
			return "Partially Delivered"
		if exceptions:
			return "Delivery Exception"
	if positive:
		if all_exceptions_resolved:
			return "Resolved with Exceptions"
		return "Partially Delivered"
	if exceptions:
		if all_exceptions_resolved:
			return "Resolved with Exceptions"
		return "Delivery Exception"
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
