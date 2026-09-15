"""Operational return-request validation and ERPNext return integration."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from vuna_transport.services.shared.security_service import (
	require_company_access,
	require_doctype_permission,
	require_document_permission,
)

RETURN_PHYSICAL_RESOLUTIONS = {
	"Return to Original Depot",
	"Return to Nearest Depot",
	"Customer Return to Depot",
}

RETURNABLE_STATUSES = {
	"Draft",
	"Requested",
	"Approved",
	"Awaiting Return",
	"In Transit",
	"Received",
	"Under Inspection",
	"Awaiting Resolution",
}
TERMINAL_STATUSES = {"Resolved", "Closed", "Cancelled"}
STATUS_TRANSITIONS = {
	"request": ("Draft", "Requested"),
	"approve": (("Draft", "Requested"), "Approved"),
	"await_return": (("Approved",), "Awaiting Return"),
	"dispatch": (("Awaiting Return",), "In Transit"),
	"receive": (("Approved", "Awaiting Return", "In Transit"), "Received"),
	"inspect": (("Received",), "Under Inspection"),
	"await_resolution": (("Under Inspection",), "Awaiting Resolution"),
	"resolve": (("Awaiting Resolution",), "Resolved"),
	"close": (("Resolved",), "Closed"),
	"cancel": (("Draft", "Requested", "Approved", "Awaiting Return"), "Cancelled"),
}


def validate_return_request(request):
	"""Validate the return against the submitted delivery and live return balance."""
	if not request.initiation_date:
		request.initiation_date = nowdate()
	if request.status not in RETURNABLE_STATUSES | TERMINAL_STATUSES:
		frappe.throw(_("Invalid Return Request status: {0}").format(request.status))
	_validate_status_change(request)
	delivery_note = _get_source_delivery_note(request)
	_populate_request_context(request, delivery_note)
	_validate_delivery_exception_context(request, delivery_note)
	_validate_return_warehouse(request)
	_validate_items(request, delivery_note)
	_validate_follow_up_documents(request, delivery_note)
	_validate_status_requirements(request)


def _validate_status_change(request):
	previous = request.get_doc_before_save()
	if previous and previous.status != request.status and not request.flags.get("allow_status_change"):
		frappe.throw(_("Use the Return Request actions to change its status."))


def _get_source_delivery_note(request):
	if not request.delivery_note:
		frappe.throw(_("Delivery Note is required."))
	delivery_note = frappe.get_doc("Delivery Note", request.delivery_note)
	require_document_permission(delivery_note, "read")
	if delivery_note.docstatus != 1 or delivery_note.is_return:
		frappe.throw(_("Return Requests must reference a submitted non-return Delivery Note."))
	return delivery_note


def _populate_request_context(request, delivery_note):
	for fieldname in ("company", "customer"):
		if request.get(fieldname) and request.get(fieldname) != delivery_note.get(fieldname):
			frappe.throw(
				_("{0} must match Delivery Note {1}.").format(
					fieldname.replace("_", " ").title(), delivery_note.name
				)
			)
		request.set(fieldname, delivery_note.get(fieldname))
	if not request.sales_order:
		request.sales_order = next(
			(row.against_sales_order for row in delivery_note.items if row.against_sales_order), None
		)
	if request.delivery_trip:
		trip = frappe.get_doc("Delivery Trip", request.delivery_trip)
		if not any(row.delivery_note == delivery_note.name for row in trip.delivery_stops):
			frappe.throw(
				_("Delivery Trip {0} does not contain Delivery Note {1}.").format(
					trip.name, delivery_note.name
				)
			)
	if request.proof_of_delivery:
		pod = frappe.get_doc("Proof of Delivery", request.proof_of_delivery)
		if pod.docstatus != 1 or pod.delivery_note != delivery_note.name:
			frappe.throw(_("Proof of Delivery must be submitted for the selected Delivery Note."))


def _validate_delivery_exception_context(request, delivery_note):
	"""Keep an exception-created return request tied to its source delivery outcome."""
	if not request.delivery_exception:
		return
	exception = frappe.get_doc("Delivery Exception", request.delivery_exception)
	require_document_permission(exception, "read")
	if exception.company != request.company or exception.customer != request.customer:
		frappe.throw(_("Delivery Exception must belong to the same Company and Customer."))
	if exception.delivery_note != delivery_note.name:
		frappe.throw(_("Delivery Exception must reference the selected Delivery Note."))
	for fieldname in ("delivery_order", "delivery_trip", "proof_of_delivery", "delivery_address"):
		expected = exception.get(fieldname)
		if expected and request.get(fieldname) and request.get(fieldname) != expected:
			frappe.throw(_("{0} must match Delivery Exception {1}.").format(fieldname.replace("_", " ").title(), exception.name))
		if expected:
			request.set(fieldname, expected)
	for fieldname in ("custody", "responsible_party", "physical_resolution", "commercial_resolution"):
		expected = exception.get(fieldname)
		if expected and request.get(fieldname) and request.get(fieldname) != expected:
			frappe.throw(_("{0} must match Delivery Exception {1}.").format(fieldname.replace("_", " ").title(), exception.name))
		if expected:
			request.set(fieldname, expected)
	request.return_warehouse = request.return_warehouse or exception.resolution_warehouse
	if exception.return_request and exception.return_request != request.name:
		frappe.throw(_("Delivery Exception {0} already has Return Request {1}.").format(exception.name, exception.return_request))


def _validate_return_warehouse(request):
	if not request.return_warehouse:
		frappe.throw(_("Return Warehouse is required."))
	warehouse_company = frappe.db.get_value("Warehouse", request.return_warehouse, "company")
	if warehouse_company and warehouse_company != request.company:
		frappe.throw(_("Return Warehouse must belong to Company {0}.").format(request.company))


def _validate_items(request, delivery_note):
	if not request.items:
		frappe.throw(_("At least one Return Request Item is required."))
	delivery_items = {row.name: row for row in delivery_note.items}
	seen = set()
	for row in request.items:
		if row.delivery_note_item in seen:
			frappe.throw(_("Delivery Note Item {0} appears more than once.").format(row.delivery_note_item))
		seen.add(row.delivery_note_item)
		delivery_item = delivery_items.get(row.delivery_note_item)
		if not delivery_item:
			frappe.throw(
				_("Item {0} does not belong to Delivery Note {1}.").format(
					row.delivery_note_item, delivery_note.name
				)
			)
		row.item_code = delivery_item.item_code
		row.item_name = delivery_item.item_name
		row.delivered_qty = abs(flt(delivery_item.stock_qty or delivery_item.qty))
		if row.delivery_order_item and delivery_item.so_detail:
			if (
				frappe.db.get_value("Delivery Order Item", row.delivery_order_item, "sales_order_item")
				!= delivery_item.so_detail
			):
				frappe.throw(
					_("Delivery Order Item does not match Delivery Note Item {0}.").format(
						row.delivery_note_item
					)
				)
		remaining = get_remaining_returnable_quantity(row.delivery_note_item, request.name)
		requested = flt(row.requested_return_qty)
		if requested <= 0 or requested > remaining:
			frappe.throw(
				_("Return quantity for {0} must be greater than zero and no more than {1}.").format(
					row.item_code, remaining
				)
			)
		row.remaining_qty = max(requested - flt(row.received_qty), 0)
		received = flt(row.received_qty)
		accepted = flt(row.accepted_return_qty)
		rejected = flt(row.rejected_return_qty)
		resolution = flt(row.resolution_qty)
		if min(received, accepted, rejected, resolution) < 0 or received > requested:
			frappe.throw(_("Return quantities for {0} are invalid.").format(row.item_code))
		if accepted + rejected > received:
			frappe.throw(
				_("Accepted and rejected quantities cannot exceed received quantity for {0}.").format(
					row.item_code
				)
			)
		if resolution > received:
			frappe.throw(
				_("Resolution quantity cannot exceed received quantity for {0}.").format(row.item_code)
			)
		if request.status != "Draft" and not row.return_reason:
			frappe.throw(
				_("Item Return Reason is required for {0} before submitting the Return Request.").format(
					row.item_code
				)
			)
		_validate_quality_inspection(row, request, delivery_note)
		_validate_resolution_type(row, request.company)


def _validate_quality_inspection(row, request, delivery_note):
	if not row.quality_inspection:
		return
	inspection = frappe.get_doc("Quality Inspection", row.quality_inspection)
	if inspection.reference_type != "Delivery Note" or inspection.reference_name != delivery_note.name:
		frappe.throw(
			_("Quality Inspection {0} must reference Delivery Note {1}.").format(
				row.quality_inspection, delivery_note.name
			)
		)
	if inspection.item_code != row.item_code:
		frappe.throw(_("Quality Inspection {0} is for a different item.").format(row.quality_inspection))


def _validate_resolution_type(row, company):
	if not row.resolution_type:
		return
	resolution = frappe.get_doc("Return Resolution Type", row.resolution_type)
	if resolution.disabled or resolution.company not in (None, "", company):
		frappe.throw(
			_("Resolution Type {0} is disabled or belongs to another Company.").format(row.resolution_type)
		)


def _validate_status_requirements(request):
	if request.status in {"Received", "Under Inspection", "Awaiting Resolution", "Resolved", "Closed"}:
		if not any(flt(row.received_qty) for row in request.items):
			frappe.throw(_("At least one returned quantity must be received before this status."))
	if request.status in {"Awaiting Resolution", "Resolved", "Closed"}:
		for row in request.items:
			if flt(row.accepted_return_qty) + flt(row.rejected_return_qty) != flt(row.received_qty):
				frappe.throw(
					_("Inspect the full received quantity for {0} before resolution.").format(row.item_code)
				)
	if request.status in {"Resolved", "Closed"}:
		for row in request.items:
			if flt(row.accepted_return_qty) and not row.resolution_type:
				frappe.throw(_("Resolution Type is required for accepted {0}.").format(row.item_code))
			if flt(row.resolution_qty) != flt(row.accepted_return_qty):
				frappe.throw(
					_("Resolution quantity must cover the accepted quantity for {0}.").format(row.item_code)
				)


def _validate_follow_up_documents(request, delivery_note):
	if request.credit_note:
		credit_note = frappe.get_doc("Sales Invoice", request.credit_note)
		if (
			credit_note.is_return != 1
			or credit_note.company != request.company
			or credit_note.customer != request.customer
		):
			frappe.throw(_("Credit Note must be a return Sales Invoice for the same Company and Customer."))
	if request.replacement_delivery_reference:
		follow_up = frappe.get_doc("Delivery Order", request.replacement_delivery_reference)
		if (
			follow_up.docstatus == 2
			or follow_up.company != request.company
			or follow_up.customer != request.customer
		):
			frappe.throw(_("Replacement Delivery Order must belong to the same Company and Customer."))
		if follow_up.name == request.delivery_note:
			frappe.throw(_("The original Delivery Note cannot be its own replacement reference."))


def get_remaining_returnable_quantity(delivery_note_item, exclude_request=None):
	"""Return submitted delivery quantity minus stock returns and active requests."""
	row = frappe.db.get_value(
		"Delivery Note Item",
		delivery_note_item,
		["parent", "stock_qty", "qty"],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("Delivery Note Item {0} was not found.").format(delivery_note_item))
	delivered = abs(flt(row.get("stock_qty") or row.get("qty")))
	returned = frappe.db.sql(
		"""
		select coalesce(sum(abs(dni.stock_qty)), 0)
		from `tabDelivery Note Item` dni
		inner join `tabDelivery Note` dn on dn.name = dni.parent
		where dni.dn_detail = %s and dn.is_return = 1 and dn.docstatus != 2
		""",
		delivery_note_item,
	)[0][0]
	requested = frappe.db.sql(
		"""
		select coalesce(sum(rri.requested_return_qty), 0)
		from `tabReturn Request Item` rri
		inner join `tabReturn Request` rr on rr.name = rri.parent
		where rri.delivery_note_item = %s
		  and rr.docstatus != 2
		  and rr.status not in ('Cancelled', 'Resolved', 'Closed')
		  and (%s is null or rr.name != %s)
		""",
		(delivery_note_item, exclude_request, exclude_request),
	)[0][0]
	return max(delivered - flt(returned) - flt(requested), 0)


@frappe.whitelist()
def get_returnable_items(delivery_note):
	"""Return submitted Delivery Note items with remaining returnable quantities."""
	require_doctype_permission("Delivery Note", "read")
	source = frappe.get_doc("Delivery Note", delivery_note)
	require_document_permission(source, "read")
	if source.docstatus != 1 or source.is_return:
		frappe.throw(_("Return Requests must reference a submitted non-return Delivery Note."))

	items = []
	for row in source.items:
		remaining = get_remaining_returnable_quantity(row.name)
		if remaining <= 0:
			continue
		delivery_order_item = row.get("delivery_order_item")
		if not delivery_order_item and row.get("so_detail"):
			delivery_order_item = frappe.db.get_value(
				"Delivery Order Item",
				{"sales_order_item": row.so_detail, "item_code": row.item_code},
				"name",
			)
		items.append(
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"delivery_note_item": row.name,
				"delivery_order_item": delivery_order_item,
				"delivered_qty": abs(flt(row.stock_qty or row.qty)),
				"requested_return_qty": remaining,
			}
		)
	return items


@frappe.whitelist()
def transition_return_request(return_request, action):
	request = frappe.get_doc("Return Request", return_request)
	require_document_permission(request, "write")
	if action not in STATUS_TRANSITIONS:
		frappe.throw(_("Unsupported Return Request action."))
	allowed, target = STATUS_TRANSITIONS[action]
	allowed = (allowed,) if isinstance(allowed, str) else allowed
	if request.status not in allowed:
		frappe.throw(_("Cannot perform {0} from status {1}.").format(action, request.status))
	previous_status = request.status
	request.flags.allow_status_change = True
	request.status = target
	request.append_activity(target, _("Return Request moved from {0} to {1}.").format(previous_status, target))
	request.save()
	return request.name


@frappe.whitelist()
def create_return_stock_document(return_request):
	"""Prepare one idempotent draft ERPNext return Delivery Note."""
	require_doctype_permission("Delivery Note", "create")
	request = frappe.get_doc("Return Request", return_request)
	require_document_permission(request, "read")
	if request.return_stock_document:
		return request.return_stock_document
	if request.status not in {"Received", "Under Inspection", "Awaiting Resolution", "Resolved"}:
		frappe.throw(_("Receive the return before preparing its stock document."))
	from erpnext.controllers.sales_and_purchase_return import make_return_doc

	source = frappe.get_doc("Delivery Note", request.delivery_note)
	target = make_return_doc("Delivery Note", source.name)
	requested_by_item = {row.delivery_note_item: row for row in request.items}
	target_items = []
	for row in target.items:
		request_item = requested_by_item.get(row.dn_detail)
		if not request_item:
			continue
		conversion_factor = flt(row.conversion_factor) or 1
		row.qty = -flt(request_item.requested_return_qty) / conversion_factor
		row.stock_qty = -flt(request_item.requested_return_qty)
		row.warehouse = request.return_warehouse
		target_items.append(row)
	target.set("items", target_items)
	if not target.items:
		frappe.throw(_("No return items could be mapped to the original Delivery Note."))
	target.insert()
	request.db_set("return_stock_document", target.name, update_modified=True)
	_link_stock_return_to_exception(request, target.name)
	return target.name


@frappe.whitelist()
def create_return_quality_inspections(return_request):
	"""Prepare draft ERPNext Quality Inspections for received return items.

	Inspection remains an explicit ERPNext activity: this action only creates the
	drafts and links them to the affected Return Request rows.
	"""
	require_doctype_permission("Quality Inspection", "create")
	request = frappe.get_doc("Return Request", return_request)
	require_document_permission(request, "write")
	if request.status not in {"Received", "Under Inspection"}:
		frappe.throw(_("Receive the return before preparing Quality Inspections."))

	inspections = []
	for row in request.items:
		if not flt(row.received_qty) or row.quality_inspection:
			continue
		inspection = frappe.get_doc(
			{
				"doctype": "Quality Inspection",
				"company": request.company,
				"inspection_type": "Incoming",
				"reference_type": "Delivery Note",
				"reference_name": request.delivery_note,
				"item_code": row.item_code,
				"batch_no": row.batch_no,
				"sample_size": row.received_qty,
				"child_row_reference": row.delivery_note_item,
			}
		)
		inspection.insert()
		row.quality_inspection = inspection.name
		inspections.append(inspection.name)

	if inspections:
		request.append_activity(
			"Quality Inspections Prepared",
			_("Prepared {0} Quality Inspection document(s).").format(len(inspections)),
		)
		request.save()
	return inspections


def _link_stock_return_to_exception(request, return_document):
	if not request.delivery_exception:
		return
	exception = frappe.get_doc("Delivery Exception", request.delivery_exception)
	if exception.return_document and exception.return_document != return_document:
		frappe.throw(_("Delivery Exception {0} already links a different Return Delivery Note.").format(exception.name))
	exception.db_set("return_document", return_document, update_modified=True)


@frappe.whitelist()
def create_return_request_from_exception(delivery_exception):
	"""Create the one physical-return case for an approved delivery exception.

	The request remains a draft. Users still review quantities and explicitly
	advance it before ERPNext stock or accounting documents are prepared.
	"""
	require_doctype_permission("Return Request", "create")
	frappe.db.sql("select name from `tabDelivery Exception` where name = %s for update", delivery_exception)
	exception = frappe.get_doc("Delivery Exception", delivery_exception)
	require_document_permission(exception, "write")
	require_company_access(exception.company)
	if exception.return_request:
		return exception.return_request
	if exception.resolution_status not in {"Open", "In Resolution"}:
		frappe.throw(_("A Return Request can only be created for an open Delivery Exception."))
	if not exception.resolution_warehouse:
		frappe.throw(_("Set Resolution Warehouse before creating a Return Request."))

	request_rows = []
	for row in exception.items:
		physical_resolution = row.physical_resolution or exception.physical_resolution
		if physical_resolution not in RETURN_PHYSICAL_RESOLUTIONS:
			continue
		quantity = flt(row.resolution_qty) or flt(row.unresolved_qty)
		if quantity <= 0:
			continue
		request_rows.append(
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"delivery_note_item": row.delivery_note_item,
				"delivery_order_item": row.delivery_order_item,
				"delivered_qty": row.delivered_qty,
				"requested_return_qty": quantity,
				"return_reason": exception.exception_reason,
			}
		)
	if not request_rows:
		frappe.throw(_("This Delivery Exception has no item approved for physical return."))

	request = frappe.new_doc("Return Request")
	request.update(
		{
			"delivery_exception": exception.name,
			"company": exception.company,
			"customer": exception.customer,
			"sales_order": frappe.db.get_value("Delivery Note", exception.delivery_note, "against_sales_order"),
			"delivery_order": exception.delivery_order,
			"delivery_note": exception.delivery_note,
			"delivery_trip": exception.delivery_trip,
			"proof_of_delivery": exception.proof_of_delivery,
			"delivery_address": exception.delivery_address,
			"custody": exception.custody,
			"responsible_party": exception.responsible_party,
			"physical_resolution": exception.physical_resolution,
			"commercial_resolution": exception.commercial_resolution,
			"return_warehouse": exception.resolution_warehouse,
			"initiated_by": "Dispatcher",
			"return_method": _return_method_from_custody(exception.custody),
			"return_remarks": exception.notes,
		}
	)
	for row in request_rows:
		request.append("items", row)
	for reason in _get_exception_rejection_reasons(exception):
		request.append("return_reasons", {"rejection_reason": reason})
	request.append_activity("Created", _("Created from Delivery Exception {0}").format(exception.name), exception)
	request.insert()
	exception.db_set("return_request", request.name, update_modified=True)
	return request.name


def _get_exception_rejection_reasons(exception):
	if not exception.proof_of_delivery:
		return []
	pod = frappe.get_doc("Proof of Delivery", exception.proof_of_delivery)
	return list(
		dict.fromkeys(
			reason.rejection_reason
			for item in pod.items
			for reason in (getattr(item, "rejection_reasons", None) or [])
			if getattr(reason, "rejection_reason", None)
		)
	)


def _return_method_from_custody(custody):
	return {
		"Still With Vehicle": "Driver Return",
		"Held By Transporter": "Third-party Return",
		"Left With Customer": "Customer Drop-off",
	}.get(custody, "Company Pickup")
