"""Delivery exception creation, resolution, and ERPNext document orchestration."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from vuna_transport.services.execution.dispatch_guard_service import (
	set_delivery_load_status,
)
from vuna_transport.services.shared.security_service import (
	require_document_permission,
)

EXCEPTION_POD_STATUSES = ("Partially Delivered", "Rejected", "Failed")
OPEN_RESOLUTION_STATUSES = ("Open", "In Resolution")
CLOSED_RESOLUTION_STATUSES = ("Resolved", "Closed")
RETURN_RESOLUTIONS = (
	"Return to Original Depot",
	"Return to Nearest Depot",
	"Customer Return to Depot",
)
FOLLOW_UP_RESOLUTIONS = ("Replacement", "Re-delivery")
POD_EXCEPTION_TYPES = {
	"Partially Delivered": "Partial Delivery",
	"Rejected": "Rejected Delivery",
	"Failed": "Failed Delivery",
}


def sync_delivery_exception_from_pod(pod):
	"""Idempotently create one active exception for a non-accepted POD stop."""
	if pod.status not in EXCEPTION_POD_STATUSES:
		return None
	# Serialize creation for the same submitted POD. The unique field on
	# Delivery Exception is the final safeguard; this lock avoids a race
	# between the existence check and insert during concurrent submissions.
	if pod.name:
		frappe.db.sql(
			"select name from `tabProof of Delivery` where name = %s for update",
			pod.name,
		)
	existing = frappe.db.get_value("Delivery Exception", {"proof_of_delivery": pod.name}, "name")
	if existing:
		return existing
	active_for_stop = frappe.db.get_value(
		"Delivery Exception",
		{
			"delivery_trip": pod.delivery_trip,
			"delivery_stop": pod.delivery_stop,
			"resolution_status": ["in", OPEN_RESOLUTION_STATUSES],
		},
		"name",
	)
	if active_for_stop:
		frappe.throw(
			_("Delivery Stop {0} already has active Delivery Exception {1}.").format(
				pod.delivery_stop, active_for_stop
			)
		)

	exception = frappe.new_doc("Delivery Exception")
	exception.proof_of_delivery = pod.name
	populate_exception_from_pod(exception, pod)
	exception.insert()
	return exception.name


def populate_exception_from_pod(exception, pod=None):
	pod = pod or frappe.get_doc("Proof of Delivery", exception.proof_of_delivery)
	require_document_permission(pod, "read")
	if pod.docstatus != 1:
		frappe.throw(_("Proof of Delivery {0} must be submitted.").format(pod.name))
	if pod.status not in EXCEPTION_POD_STATUSES:
		frappe.throw(_("Accepted Proofs of Delivery do not require a Delivery Exception."))

	for fieldname in (
		"company",
		"delivery_load",
		"delivery_trip",
		"delivery_stop",
		"delivery_order",
		"delivery_note",
		"customer",
		"delivery_address",
	):
		exception.set(fieldname, pod.get(fieldname))
	exception.exception_type = POD_EXCEPTION_TYPES[pod.status]
	exception.exception_reason = exception.exception_reason or pod.exception_reason

	source_rows = _get_pod_exception_rows(pod)
	if not source_rows:
		frappe.throw(_("The Proof of Delivery has no unresolved item quantities."))
	if not exception.items:
		for values in source_rows.values():
			exception.append("items", values)
		return

	current_keys = {row.delivery_note_item for row in exception.items}
	if current_keys != set(source_rows):
		frappe.throw(_("Delivery Exception items must match the unresolved POD items."))
	for row in exception.items:
		values = source_rows[row.delivery_note_item]
		for fieldname in (
			"item_code",
			"item_name",
			"stock_uom",
			"delivery_order_item",
			"delivery_note_item",
			"planned_qty",
			"delivered_qty",
			"rejected_qty",
			"unresolved_qty",
		):
			row.set(fieldname, values[fieldname])


def _get_pod_exception_rows(pod):
	delivery_order_item_names = [row.delivery_order_item for row in pod.items if row.delivery_order_item]
	stock_uoms = (
		dict(
			frappe.get_all(
				"Delivery Order Item",
				filters={"name": ["in", delivery_order_item_names]},
				fields=["name", "stock_uom"],
				as_list=True,
			)
		)
		if delivery_order_item_names
		else {}
	)
	rows = {}
	for pod_item in pod.items:
		values = get_exception_item_values(pod_item, stock_uoms.get(pod_item.delivery_order_item))
		if values["unresolved_qty"] > 0:
			rows[pod_item.delivery_note_item] = values
	return rows


def get_exception_item_values(pod_item, stock_uom=None):
	planned = flt(pod_item.ordered_qty)
	delivered = flt(pod_item.delivered_qty)
	return {
		"item_code": pod_item.item_code,
		"item_name": getattr(pod_item, "item_name", None),
		"stock_uom": stock_uom,
		"delivery_order_item": pod_item.delivery_order_item,
		"delivery_note_item": pod_item.delivery_note_item,
		"planned_qty": planned,
		"delivered_qty": delivered,
		"rejected_qty": flt(pod_item.rejected_qty),
		"unresolved_qty": max(planned - delivered, 0),
	}


def validate_delivery_exception(exception):
	populate_exception_from_pod(exception)
	_validate_quantities(exception)
	_validate_resolution(exception)
	_validate_erpnext_links(exception)
	_validate_follow_up_delivery(exception)


def _validate_quantities(exception):
	total_unresolved = 0
	total_resolution = 0
	for row in exception.items:
		unresolved = flt(row.unresolved_qty)
		resolution = flt(row.resolution_qty)
		if resolution < 0 or resolution > unresolved:
			frappe.throw(
				_("Resolution quantity for item {0} must be between 0 and {1}.").format(
					row.item_code, unresolved
				)
			)
		total_unresolved += unresolved
		total_resolution += resolution
	exception.total_unresolved_quantity = total_unresolved
	exception.total_resolution_quantity = total_resolution


def _validate_resolution(exception):
	if exception.resolution_status == "Open":
		return
	if not exception.responsible_user:
		frappe.throw(_("Responsible User is required once exception resolution begins."))

	return_required = False
	credit_required = False
	follow_up_required = False
	for row in exception.items:
		custody = row.custody or exception.custody
		responsible_party = row.responsible_party or exception.responsible_party
		physical = row.physical_resolution or exception.physical_resolution
		commercial = row.commercial_resolution or exception.commercial_resolution
		if flt(row.resolution_qty) and (
			custody == "Unknown"
			or responsible_party == "Unknown"
			or physical == "Pending Decision"
			or commercial == "Pending"
		):
			frappe.throw(
				_("Set custody, responsible party, and resolutions for item {0}.").format(row.item_code)
			)
		return_required = return_required or physical in RETURN_RESOLUTIONS
		credit_required = credit_required or commercial == "Credit Note"
		follow_up_required = follow_up_required or commercial in FOLLOW_UP_RESOLUTIONS

	if return_required and not exception.resolution_warehouse:
		frappe.throw(_("Resolution Warehouse is required for depot returns."))

	if exception.resolution_status not in CLOSED_RESOLUTION_STATUSES:
		return
	if not exception.resolved_on:
		frappe.throw(_("Resolved On is required for a resolved or closed exception."))
	for row in exception.items:
		if flt(row.resolution_qty) != flt(row.unresolved_qty):
			frappe.throw(
				_("Resolve the full unresolved quantity for item {0} before closing.").format(row.item_code)
			)
	if return_required and not exception.return_document:
		frappe.throw(_("Link the submitted return Delivery Note before resolving."))
	if credit_required and not exception.credit_note:
		frappe.throw(_("Link the submitted Credit Note before resolving."))
	if follow_up_required and not exception.replacement_delivery_reference:
		frappe.throw(_("Link the follow-up Delivery Order before resolving."))


def _validate_erpnext_links(exception):
	require_submitted = exception.resolution_status in CLOSED_RESOLUTION_STATUSES
	if exception.return_document:
		return_document = frappe.get_doc("Delivery Note", exception.return_document)
		if not return_document.is_return or return_document.return_against != exception.delivery_note:
			frappe.throw(
				_("Return Delivery Note must be a return against {0}.").format(exception.delivery_note)
			)
		_validate_company_customer(exception, return_document, _("Return Delivery Note"))
		_validate_return_quantities(exception, return_document, require_submitted)
		if require_submitted and return_document.docstatus != 1:
			frappe.throw(_("Submit Return Delivery Note {0} first.").format(return_document.name))

	if exception.credit_note:
		credit_note = frappe.get_doc("Sales Invoice", exception.credit_note)
		if not credit_note.is_return:
			frappe.throw(_("Sales Invoice {0} is not a Credit Note.").format(credit_note.name))
		_validate_company_customer(exception, credit_note, _("Credit Note"))
		if require_submitted and credit_note.docstatus != 1:
			frappe.throw(_("Submit Credit Note {0} first.").format(credit_note.name))


def _validate_company_customer(exception, document, label):
	if document.company != exception.company or document.customer != exception.customer:
		frappe.throw(_("{0} belongs to a different Company or Customer.").format(label))


def _validate_return_quantities(exception, return_document, require_complete=False):
	allowed_by_delivery_note_item = defaultdict(float)
	for row in exception.items:
		physical = row.physical_resolution or exception.physical_resolution
		if physical in RETURN_RESOLUTIONS:
			allowed_by_delivery_note_item[row.delivery_note_item] += flt(row.resolution_qty)
	returned_by_delivery_note_item = defaultdict(float)
	for row in getattr(return_document, "items", []) or []:
		source_item = getattr(row, "dn_detail", None)
		if source_item:
			if (
				require_complete
				and exception.resolution_warehouse
				and getattr(row, "warehouse", None) != exception.resolution_warehouse
			):
				frappe.throw(
					_("Return item {0} must be received into Resolution Warehouse {1}.").format(
						source_item, exception.resolution_warehouse
					)
				)
			returned_by_delivery_note_item[source_item] += abs(
				flt(getattr(row, "stock_qty", None) or row.qty)
			)
	for source_item, quantity in returned_by_delivery_note_item.items():
		if quantity > allowed_by_delivery_note_item[source_item]:
			frappe.throw(
				_(
					"Return quantity for Delivery Note Item {0} exceeds the approved exception quantity."
				).format(source_item)
			)
	if require_complete:
		for source_item, approved in allowed_by_delivery_note_item.items():
			if returned_by_delivery_note_item[source_item] < approved:
				frappe.throw(
					_("Return Delivery Note does not cover the full approved quantity for item {0}.").format(
						source_item
					)
				)


def _validate_follow_up_delivery(exception):
	if not exception.replacement_delivery_reference:
		return
	if exception.resolution_status == "Open":
		frappe.throw(_("Move the exception to In Resolution before linking follow-up demand."))
	follow_up = frappe.get_doc("Delivery Order", exception.replacement_delivery_reference)
	if follow_up.name == exception.delivery_order:
		frappe.throw(_("The original Delivery Order cannot be its own follow-up delivery."))
	if follow_up.docstatus == 2:
		frappe.throw(_("The follow-up Delivery Order is cancelled."))
	if follow_up.company != exception.company or follow_up.customer != exception.customer:
		frappe.throw(_("The follow-up Delivery Order belongs to a different Company or Customer."))
	allowed_by_item = defaultdict(float)
	for row in exception.items:
		commercial = row.commercial_resolution or exception.commercial_resolution
		if commercial in FOLLOW_UP_RESOLUTIONS:
			allowed_by_item[row.item_code] += flt(row.resolution_qty)
	follow_up_by_item = defaultdict(float)
	for row in follow_up.items:
		follow_up_by_item[row.item_code] += flt(row.transport_qty)
	for item_code, quantity in follow_up_by_item.items():
		if quantity > allowed_by_item[item_code]:
			frappe.throw(
				_("Follow-up quantity for item {0} exceeds the approved exception quantity.").format(
					item_code
				)
			)
	if not follow_up_by_item or sum(follow_up_by_item.values()) <= 0:
		frappe.throw(_("The follow-up Delivery Order has no transport quantity."))
	if exception.resolution_status in CLOSED_RESOLUTION_STATUSES:
		for item_code, quantity in allowed_by_item.items():
			if follow_up_by_item[item_code] != quantity:
				frappe.throw(
					_("Follow-up Delivery Order does not cover the approved quantity for item {0}.").format(
						item_code
					)
				)
	if exception.resolution_warehouse and follow_up.source_warehouse != exception.resolution_warehouse:
		frappe.throw(
			_("Follow-up Delivery Order must use Resolution Warehouse {0} as its source.").format(
				exception.resolution_warehouse
			)
		)


def cancel_delivery_exception_from_pod(pod):
	name = frappe.db.get_value("Delivery Exception", {"proof_of_delivery": pod.name}, "name")
	if not name:
		return
	frappe.db.set_value(
		"Delivery Exception",
		name,
		{
			"resolution_status": "Closed",
			"resolved_on": now_datetime(),
			"notes": _("Source Proof of Delivery {0} was cancelled.").format(pod.name),
		},
		update_modified=True,
	)


def recompute_exception_context(exception):
	if not getattr(exception, "delivery_load", None):
		return
	recompute_delivery_load_completion(exception.delivery_load, exception.delivery_trip)
	if getattr(exception, "delivery_order", None):
		from vuna_transport.services.execution.pod_service import (
			recompute_delivery_order_status,
		)

		recompute_delivery_order_status(exception.delivery_order)


def recompute_delivery_load_completion(delivery_load, delivery_trip=None):
	if not delivery_load or not frappe.db.exists("Delivery Load", delivery_load):
		return
	open_exception = frappe.db.get_value(
		"Delivery Exception",
		{
			"delivery_load": delivery_load,
			"resolution_status": ["in", OPEN_RESOLUTION_STATUSES],
		},
		"name",
	)
	if open_exception:
		set_delivery_load_status(
			delivery_load,
			"Exception Resolution",
			_("Delivery Exception {0} requires resolution").format(open_exception),
		)
		return

	trip_name = delivery_trip or frappe.db.get_value(
		"Delivery Trip",
		{"vuna_delivery_load": delivery_load, "docstatus": ["!=", 2]},
		"name",
	)
	if not trip_name:
		return
	stops = frappe.get_all(
		"Delivery Stop",
		filters={"parent": trip_name, "parenttype": "Delivery Trip"},
		fields=["visited"],
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
	target = (
		"Completed"
		if pod_statuses and all(status == "Accepted" for status in pod_statuses)
		else "Completed with Exceptions"
	)
	set_delivery_load_status(
		delivery_load,
		target,
		_("Every delivery stop is complete and no Delivery Exception remains open"),
	)


def get_delivery_order_exception_outcomes(delivery_order):
	if not frappe.db.table_exists("Delivery Exception"):
		return []
	exceptions = frappe.get_all(
		"Delivery Exception",
		filters={"delivery_order": delivery_order},
		fields=["name", "resolution_status", "total_unresolved_quantity"],
	)
	return exceptions


def has_open_delivery_exceptions(delivery_load):
	if not frappe.db.table_exists("Delivery Exception"):
		return False
	return bool(
		frappe.db.get_value(
			"Delivery Exception",
			{
				"delivery_load": delivery_load,
				"resolution_status": ["in", OPEN_RESOLUTION_STATUSES],
			},
			"name",
		)
	)


def validate_exception_deletion(exception):
	if frappe.db.get_value("Proof of Delivery", exception.proof_of_delivery, "docstatus") == 1:
		frappe.throw(_("Cancel the submitted Proof of Delivery before deleting its Delivery Exception."))
