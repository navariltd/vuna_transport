"""Operational return-request validation and ERPNext return integration."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, flt, now_datetime, nowdate

from vuna_transport.services.execution.stock_identity_service import (
	get_delivery_note_item_batches,
	get_single_delivery_note_item_batch,
)
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

ACTIVE_STATUSES = {
	"Draft",
	"Awaiting Receipt",
	"Awaiting Sales Return",
	"Awaiting Inspection",
	"Awaiting Disposition",
	"Awaiting Return Submission",
	"Awaiting Commercial Resolution",
}


def _return_request_qty_precision() -> int:
	return frappe.get_precision("Return Request Item", "requested_return_qty") or 3


def _round_return_request_qty(quantity) -> float:
	return flt(quantity, _return_request_qty_precision())


TERMINAL_STATUSES = {"Resolved", "Closed", "Cancelled"}
VALID_STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES

# A Sales Return reverses the commercial terms that were executed on its source
# Delivery Note. These fields are deliberately limited to ERPNext's standard
# selling fields: transport does not own or recalculate pricing rules.
RETURN_SALES_ITEM_COMMERCIAL_FIELDS = (
	"price_list_rate",
	"base_price_list_rate",
	"margin_type",
	"margin_rate_or_amount",
	"rate_with_margin",
	"base_rate_with_margin",
	"discount_percentage",
	"discount_amount",
	"rate",
	"base_rate",
	"pricing_rules",
)


def validate_return_request(request):
	"""Validate the return against the submitted delivery and live return balance."""
	if not request.initiation_date:
		request.initiation_date = nowdate()
	if request.status not in VALID_STATUSES:
		frappe.throw(_("Invalid Return Request status: {0}").format(request.status))
	source_type, source = _get_return_source(request)
	_populate_request_context(request, source, source_type)
	_set_return_source(request)
	_validate_return_warehouse(request)
	_validate_items(request, source, source_type)
	_validate_follow_up_documents(request, source, source_type)
	_apply_return_state(request, get_return_request_state(request))


def _set_return_source(request):
	if request.proof_of_delivery:
		pod_status = frappe.db.get_value("Proof of Delivery", request.proof_of_delivery, "status")
		request.return_source = "Failed Delivery" if pod_status == "Failed" else "Delivery Rejection"
	elif not request.return_source:
		request.return_source = "Manual"


def _get_return_source(request):
	"""Resolve one authoritative return source without guessing across deliveries."""
	if request.delivery_note:
		delivery_note = frappe.get_doc("Delivery Note", request.delivery_note)
		require_document_permission(delivery_note, "read")
		if delivery_note.docstatus != 1 or delivery_note.is_return:
			frappe.throw(_("Return Requests must reference a submitted non-return Delivery Note."))
		return "Delivery Note", delivery_note
	if not request.sales_invoice:
		frappe.throw(_("Select a Delivery Note or Sales Invoice."))
	sales_invoice = frappe.get_doc("Sales Invoice", request.sales_invoice)
	require_document_permission(sales_invoice, "read")
	if sales_invoice.docstatus != 1 or sales_invoice.is_return:
		frappe.throw(_("Return Requests must reference a submitted non-return Sales Invoice."))
	if sales_invoice.update_stock:
		return "Sales Invoice", sales_invoice
	delivery_notes = sorted({row.delivery_note for row in sales_invoice.items if row.delivery_note})
	if len(delivery_notes) != 1:
		frappe.throw(
			_(
				"Sales Invoice {0} is linked to multiple or no Delivery Notes. Select the Delivery Note to return."
			).format(sales_invoice.name)
		)
	request.delivery_note = delivery_notes[0]
	return _get_return_source(request)


def _populate_request_context(request, source, source_type):
	for fieldname in ("company", "customer"):
		if request.get(fieldname) and request.get(fieldname) != source.get(fieldname):
			frappe.throw(
				_("{0} must match Delivery Note {1}.").format(
					fieldname.replace("_", " ").title(), source.name
				)
			)
		request.set(fieldname, source.get(fieldname))
	if not request.sales_order:
		request.sales_order = next(
			(
				row.against_sales_order if source_type == "Delivery Note" else row.sales_order
				for row in source.items
				if (row.against_sales_order if source_type == "Delivery Note" else row.sales_order)
			),
			None,
		)
	if source_type == "Delivery Note" and not request.sales_invoice:
		sales_invoices = _get_source_sales_invoices(source.name)
		if len(sales_invoices) == 1:
			request.sales_invoice = sales_invoices[0]
	if source_type == "Delivery Note" and request.delivery_trip:
		trip = frappe.get_doc("Delivery Trip", request.delivery_trip)
		if not any(row.delivery_note == source.name for row in trip.delivery_stops):
			frappe.throw(
				_("Delivery Trip {0} does not contain Delivery Note {1}.").format(trip.name, source.name)
			)
	if source_type == "Delivery Note" and request.proof_of_delivery:
		pod = frappe.get_doc("Proof of Delivery", request.proof_of_delivery)
		if pod.docstatus != 1 or pod.delivery_note != source.name:
			frappe.throw(_("Proof of Delivery must be submitted for the selected Delivery Note."))


def _validate_return_warehouse(request):
	if not request.return_warehouse:
		if request.docstatus == 1:
			frappe.throw(_("Return Warehouse is required before submitting the Return Request."))
		return
	warehouse_company = frappe.db.get_value("Warehouse", request.return_warehouse, "company")
	if warehouse_company and warehouse_company != request.company:
		frappe.throw(_("Return Warehouse must belong to Company {0}.").format(request.company))


def _validate_items(request, source, source_type):
	if not request.items:
		frappe.throw(_("At least one Return Request Item is required."))
	source_items = {row.name: row for row in source.items}
	seen = set()
	for row in request.items:
		reference_field = "delivery_note_item" if source_type == "Delivery Note" else "sales_invoice_item"
		reference = row.get(reference_field)
		if not reference or reference in seen:
			frappe.throw(_("Each return item must reference one unique source item."))
		seen.add(reference)
		source_item = source_items.get(reference)
		if not source_item:
			frappe.throw(
				_("Item {0} does not belong to {1} {2}.").format(reference, source_type, source.name)
			)
		row.item_code = source_item.item_code
		row.item_name = source_item.item_name
		row.uom = source_item.uom
		row.stock_uom = source_item.stock_uom
		row.conversion_factor = flt(source_item.conversion_factor) or 1
		if source_type == "Delivery Note":
			source_batches = get_delivery_note_item_batches(source_item)
			if row.batch_no and row.batch_no not in source_batches:
				frappe.throw(
					_("Batch {0} was not delivered on Delivery Note Item {1}.").format(
						row.batch_no, source_item.name
					)
				)
			if len(source_batches) == 1:
				row.batch_no = source_batches[0]
		row.delivered_qty = abs(flt(source_item.qty))
		if source_type == "Delivery Note" and row.delivery_order_item and source_item.so_detail:
			if (
				frappe.db.get_value("Delivery Order Item", row.delivery_order_item, "sales_order_item")
				!= source_item.so_detail
			):
				frappe.throw(
					_("Delivery Order Item does not match Delivery Note Item {0}.").format(
						row.delivery_note_item
					)
				)
		remaining = (
			get_remaining_returnable_quantity(reference, request.name)
			if source_type == "Delivery Note"
			else get_remaining_returnable_sales_invoice_quantity(reference, request.name)
		)
		remaining = _round_return_request_qty(remaining)
		requested = flt(row.requested_return_qty, row.precision("requested_return_qty"))
		row.requested_return_qty = requested
		if requested <= 0 or requested > remaining:
			frappe.throw(
				_("Return quantity for {0} must be greater than zero and no more than {1}.").format(
					row.item_code, remaining
				)
			)
		row.remaining_qty = flt(max(requested - flt(row.received_qty), 0), row.precision("remaining_qty"))
		received = flt(row.received_qty)
		accepted = flt(row.accepted_return_qty)
		rejected = flt(row.rejected_return_qty)
		if min(received, accepted, rejected) < 0 or received > requested:
			frappe.throw(_("Return quantities for {0} are invalid.").format(row.item_code))
		if accepted + rejected > requested:
			frappe.throw(
				_("Inspection quantities cannot exceed expected return quantity for {0}.").format(
					row.item_code
				)
			)
		if request.docstatus == 1 and not row.return_reason:
			frappe.throw(
				_("Item Return Reason is required for {0} before submitting the Return Request.").format(
					row.item_code
				)
			)
		_validate_quality_inspection(row, request, source)


def _validate_quality_inspection(row, request, source):
	if not row.quality_inspection:
		return
	inspection = frappe.get_doc("Quality Inspection", row.quality_inspection)
	expected_reference = getattr(request, "return_stock_document", None) or getattr(
		request, "credit_note", None
	)
	if not expected_reference:
		frappe.throw(_("Create the draft Sales Return before linking Quality Inspections."))
	expected_type = "Delivery Note" if request.delivery_note else "Sales Invoice"
	if inspection.reference_type != expected_type or inspection.reference_name != expected_reference:
		frappe.throw(
			_("Quality Inspection {0} must reference {1} {2}.").format(
				row.quality_inspection, expected_type, expected_reference
			)
		)
	if inspection.item_code != row.item_code:
		frappe.throw(_("Quality Inspection {0} is for a different item.").format(row.quality_inspection))


def _validate_follow_up_documents(request, source, source_type):
	if request.sales_invoice:
		sales_invoice = frappe.get_doc("Sales Invoice", request.sales_invoice)
		if (
			sales_invoice.docstatus != 1
			or sales_invoice.is_return
			or sales_invoice.company != request.company
			or sales_invoice.customer != request.customer
		):
			frappe.throw(_("Sales Invoice must be submitted for the same Company and Customer."))
	if request.credit_note:
		credit_note = frappe.get_doc("Sales Invoice", request.credit_note)
		if (
			credit_note.is_return != 1
			or credit_note.company != request.company
			or credit_note.customer != request.customer
		):
			frappe.throw(_("Credit Note must be a return Sales Invoice for the same Company and Customer."))
	if request.replacement_sales_order:
		replacement = frappe.get_doc("Sales Order", request.replacement_sales_order)
		if (
			replacement.docstatus == 2
			or replacement.company != request.company
			or replacement.customer != request.customer
		):
			frappe.throw(_("Replacement Sales Order must belong to the same Company and Customer."))
	if request.refund_payment_entry:
		payment = frappe.get_doc("Payment Entry", request.refund_payment_entry)
		if payment.docstatus == 2 or payment.company != request.company:
			frappe.throw(_("Refund Payment Entry must belong to the same Company."))
	if request.replacement_delivery_reference:
		follow_up = frappe.get_doc("Delivery Order", request.replacement_delivery_reference)
		if (
			follow_up.docstatus == 2
			or follow_up.company != request.company
			or follow_up.customer != request.customer
		):
			frappe.throw(_("Replacement Delivery Order must belong to the same Company and Customer."))
		if request.delivery_note and follow_up.name == request.delivery_note:
			frappe.throw(_("The original Delivery Note cannot be its own replacement reference."))


def _get_source_sales_invoices(delivery_note):
	return frappe.get_all(
		"Sales Invoice Item",
		filters={"delivery_note": delivery_note, "docstatus": 1},
		pluck="parent",
		distinct=True,
	)


def get_return_request_state(request):
	"""Derive return progress from ERPNext documents instead of browser-maintained status."""
	if request.get("process_version") == "Custody and Inspection":
		return _get_v2_return_request_state(request)
	has_delivery_note = bool(request.get("delivery_note"))
	reference_field = "delivery_note_item" if has_delivery_note else "sales_invoice_item"
	if not has_delivery_note and any(getattr(row, "delivery_note_item", None) for row in request.items):
		# Keeps state calculation compatible with legacy requests created before
		# Sales Invoice items were introduced as an alternate source.
		has_delivery_note = True
		reference_field = "delivery_note_item"
	expected_by_item = {
		getattr(row, reference_field, None): flt(row.requested_return_qty) for row in request.items
	}
	returned_by_item = dict.fromkeys(expected_by_item, 0.0)
	returned_stock_by_item = dict.fromkeys(expected_by_item, 0.0)
	request_items = {getattr(row, reference_field, None): row for row in request.items}
	return_docstatus = None

	if request.return_stock_document and frappe.db.exists("Delivery Note", request.return_stock_document):
		return_docstatus = frappe.db.get_value("Delivery Note", request.return_stock_document, "docstatus")
		if return_docstatus == 1:
			for row in frappe.get_all(
				"Delivery Note Item",
				filters={"parent": request.return_stock_document},
				fields=["dn_detail", "stock_qty", "qty"],
			):
				if row.dn_detail in returned_by_item:
					stock_qty = abs(flt(row.stock_qty or row.qty))
					returned_stock_by_item[row.dn_detail] += stock_qty
					returned_by_item[row.dn_detail] += _from_stock_qty(
						stock_qty, request_items[row.dn_detail]
					)
	elif not has_delivery_note and request.credit_note:
		return_docstatus = frappe.db.get_value("Sales Invoice", request.credit_note, "docstatus")
		if return_docstatus == 1:
			for row in frappe.get_all(
				"Sales Invoice Item",
				filters={"parent": request.credit_note},
				fields=["sales_invoice_item", "stock_qty", "qty"],
			):
				if row.sales_invoice_item in returned_by_item:
					stock_qty = abs(flt(row.stock_qty or row.qty))
					returned_stock_by_item[row.sales_invoice_item] += stock_qty
					returned_by_item[row.sales_invoice_item] += _from_stock_qty(
						stock_qty, request_items[row.sales_invoice_item]
					)

	inspection_complete = True
	if request.inspection_required:
		inspection_names = [row.quality_inspection for row in request.items if row.quality_inspection]
		inspection_statuses = {}
		if inspection_names:
			inspection_statuses = {
				row.name: (row.docstatus, row.status)
				for row in frappe.get_all(
					"Quality Inspection",
					filters={"name": ["in", inspection_names]},
					fields=["name", "docstatus", "status"],
				)
			}
		for row in request.items:
			inspection = inspection_statuses.get(row.quality_inspection)
			if not inspection or inspection[0] != 1 or inspection[1] not in {"Accepted", "Rejected"}:
				inspection_complete = False
				break

	commercial_complete = _has_completed_commercial_resolution(request)
	if request.docstatus == 0:
		status = "Draft"
	elif request.docstatus == 2:
		status = "Cancelled"
	elif (
		(has_delivery_note and not request.return_stock_document)
		or (not has_delivery_note and not request.credit_note)
		or return_docstatus in (None, 2)
	):
		status = "Awaiting Sales Return"
	elif return_docstatus == 0:
		status = (
			"Awaiting Inspection"
			if request.inspection_required and not inspection_complete
			else "Awaiting Return Submission"
		)
	elif commercial_complete:
		status = "Closed" if request.status == "Closed" else "Resolved"
	else:
		status = "Awaiting Commercial Resolution"

	expected = sum(expected_by_item.values())
	returned = sum(returned_by_item.values())
	return {
		"status": status,
		"expected_return_qty": expected,
		"returned_qty": returned,
		"return_difference_qty": max(expected - returned, 0),
		"returned_by_item": returned_by_item,
		"returned_stock_by_item": returned_stock_by_item,
		"return_docstatus": return_docstatus,
		"inspection_complete": inspection_complete,
		"commercial_complete": commercial_complete,
	}


def _get_v2_return_request_state(request):
	"""Derive V2 progress from the linked Sales Return and inspection."""
	return _get_v2_sales_return_state(request)


def _get_v2_sales_return_state(request):
	request_items = {row.delivery_note_item: row for row in request.items}
	returned_by_item = dict.fromkeys(request_items, 0.0)
	returned_stock_by_item = dict.fromkeys(request_items, 0.0)
	return_docstatus = frappe.db.get_value("Delivery Note", request.return_stock_document, "docstatus")
	if return_docstatus == 1:
		for row in frappe.get_all(
			"Delivery Note Item",
			filters={"parent": request.return_stock_document, "stock_qty": ["<", 0]},
			fields=["dn_detail", "stock_qty"],
		):
			if row.dn_detail not in request_items:
				continue
			stock_qty = abs(flt(row.stock_qty))
			returned_stock_by_item[row.dn_detail] += stock_qty
			returned_by_item[row.dn_detail] += _from_stock_qty(stock_qty, request_items[row.dn_detail])
	inspection = frappe.db.get_value(
		"Return Inspection",
		{"sales_return": request.return_stock_document, "docstatus": 1},
		["name", "stock_entry"],
		as_dict=True,
	)
	expected = sum(flt(row.requested_return_qty) for row in request.items)
	returned = sum(returned_by_item.values())
	if request.docstatus == 0:
		status = "Draft"
	elif request.docstatus == 2:
		status = "Cancelled"
	elif not request.return_stock_document or return_docstatus in (None, 2):
		status = "Awaiting Sales Return"
	elif return_docstatus == 0:
		status = "Awaiting Return Submission"
	elif not inspection:
		status = "Awaiting Inspection"
	elif (
		not inspection.stock_entry
		or frappe.db.get_value("Stock Entry", inspection.stock_entry, "docstatus") != 1
	):
		status = "Awaiting Disposition"
	elif _has_completed_commercial_resolution(request):
		status = "Closed" if request.status == "Closed" else "Resolved"
	else:
		status = "Awaiting Commercial Resolution"
	return {
		"status": status,
		"expected_return_qty": expected,
		"returned_qty": returned,
		"return_difference_qty": max(expected - returned, 0),
		"returned_by_item": returned_by_item,
		"returned_stock_by_item": returned_stock_by_item,
		"return_docstatus": return_docstatus,
		"inspection_complete": bool(inspection),
		"commercial_complete": _has_completed_commercial_resolution(request),
	}


def _has_completed_commercial_resolution(request):
	if request.no_further_action:
		return bool(request.no_further_action_remarks and request.resolved_by and request.resolved_on)
	for doctype, fieldname in (
		("Sales Invoice", "credit_note"),
		("Sales Order", "replacement_sales_order"),
		("Payment Entry", "refund_payment_entry"),
		("Delivery Order", "replacement_delivery_reference"),
	):
		name = request.get(fieldname)
		if name and frappe.db.get_value(doctype, name, "docstatus") == 1:
			return True
	return False


def _apply_return_state(request, state):
	request.status = state["status"]
	request.expected_return_qty = state["expected_return_qty"]
	request.returned_qty = state["returned_qty"]
	request.return_difference_qty = state["return_difference_qty"]
	for row in request.items:
		row.received_qty = state["returned_by_item"].get(row.delivery_note_item or row.sales_invoice_item, 0)
		row.remaining_qty = max(flt(row.requested_return_qty) - flt(row.received_qty), 0)


def recalculate_return_request(return_request):
	"""Persist state derived from the linked Sales Return, inspections and commercial documents."""
	if not return_request or not frappe.db.exists("Return Request", return_request):
		return None
	request = frappe.get_doc("Return Request", return_request)
	state = get_return_request_state(request)
	_apply_return_state(request, state)
	frappe.db.set_value(
		"Return Request",
		request.name,
		{
			"status": request.status,
			"expected_return_qty": request.expected_return_qty,
			"returned_qty": request.returned_qty,
			"return_difference_qty": request.return_difference_qty,
		},
		update_modified=False,
	)
	for row in request.items:
		frappe.db.set_value(
			"Return Request Item",
			row.name,
			{"received_qty": row.received_qty, "remaining_qty": row.remaining_qty},
			update_modified=False,
		)
	return state


def get_remaining_returnable_quantity(delivery_note_item, exclude_request=None):
	"""Return remaining quantity in the Delivery Note item's transaction UOM."""
	row = frappe.db.get_value(
		"Delivery Note Item",
		delivery_note_item,
		["parent", "stock_qty", "qty", "conversion_factor"],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("Delivery Note Item {0} was not found.").format(delivery_note_item))
	conversion_factor = flt(row.get("conversion_factor")) or 1
	delivered = abs(flt(row.get("qty")))
	returned_stock = frappe.db.sql(
		"""
		select coalesce(sum(abs(dni.stock_qty)), 0)
		from `tabDelivery Note Item` dni
		inner join `tabDelivery Note` dn on dn.name = dni.parent
		where dni.dn_detail = %s and dn.is_return = 1 and dn.docstatus = 1
		""",
		delivery_note_item,
	)[0][0]
	returned = flt(returned_stock) / conversion_factor
	requested = frappe.db.sql(
		"""
		select coalesce(sum(greatest(
			rri.requested_return_qty - coalesce((
				select sum(abs(return_item.stock_qty)) / %s
				from `tabDelivery Note Item` return_item
				inner join `tabDelivery Note` return_dn on return_dn.name = return_item.parent
				where return_dn.name = rr.return_stock_document
				  and return_dn.docstatus = 1
				  and return_item.dn_detail = rri.delivery_note_item
			), 0), 0)), 0)
		from `tabReturn Request Item` rri
		inner join `tabReturn Request` rr on rr.name = rri.parent
		where rri.delivery_note_item = %s
		  and rr.docstatus != 2
		  and (%s is null or rr.name != %s)
		""",
		(conversion_factor, delivery_note_item, exclude_request, exclude_request),
	)[0][0]
	return max(delivered - flt(returned) - flt(requested), 0)


def get_remaining_returnable_sales_invoice_quantity(sales_invoice_item, exclude_request=None):
	"""Return remaining quantity in a stock-updating Sales Invoice item's UOM."""
	row = frappe.db.get_value(
		"Sales Invoice Item", sales_invoice_item, ["qty", "conversion_factor"], as_dict=True
	)
	if not row:
		frappe.throw(_("Sales Invoice Item {0} was not found.").format(sales_invoice_item))
	conversion_factor = flt(row.conversion_factor) or 1
	delivered = abs(flt(row.qty))
	returned_stock = frappe.db.sql(
		"""
		select coalesce(sum(abs(sii.stock_qty)), 0)
		from `tabSales Invoice Item` sii
		inner join `tabSales Invoice` si on si.name = sii.parent
		where sii.sales_invoice_item = %s and si.is_return = 1 and si.docstatus = 1
		""",
		sales_invoice_item,
	)[0][0]
	returned = flt(returned_stock) / conversion_factor
	requested = frappe.db.sql(
		"""
		select coalesce(sum(rri.requested_return_qty), 0)
		from `tabReturn Request Item` rri
		inner join `tabReturn Request` rr on rr.name = rri.parent
		where rri.sales_invoice_item = %s and rr.docstatus != 2
		and (%s is null or rr.name != %s)
		""",
		(sales_invoice_item, exclude_request, exclude_request),
	)[0][0]
	return max(delivered - returned - flt(requested), 0)


@frappe.whitelist()
def get_returnable_items(delivery_note, exclude_request=None):
	"""Return submitted Delivery Note items with remaining returnable quantities."""
	require_doctype_permission("Delivery Note", "read")
	source = frappe.get_doc("Delivery Note", delivery_note)
	require_document_permission(source, "read")
	if source.docstatus != 1 or source.is_return:
		frappe.throw(_("Return Requests must reference a submitted non-return Delivery Note."))
	if exclude_request:
		request = frappe.get_doc("Return Request", exclude_request)
		require_document_permission(request, "read")
		if request.delivery_note != source.name:
			frappe.throw(
				_("Return Request {0} does not reference Delivery Note {1}.").format(
					request.name, source.name
				)
			)

	items = []
	for row in source.items:
		remaining = _round_return_request_qty(get_remaining_returnable_quantity(row.name, exclude_request))
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
				"delivered_qty": abs(flt(row.qty)),
				"requested_return_qty": remaining,
				"uom": row.uom,
				"stock_uom": row.stock_uom,
				"conversion_factor": flt(row.conversion_factor) or 1,
				"batch_no": get_single_delivery_note_item_batch(row),
			}
		)
	return items


@frappe.whitelist()
def get_sales_invoice_return_context(sales_invoice, exclude_request=None):
	"""Resolve a Sales Invoice into its Delivery Note or stock-return item context."""
	require_doctype_permission("Sales Invoice", "read")
	source = frappe.get_doc("Sales Invoice", sales_invoice)
	require_document_permission(source, "read")
	require_company_access(source.company)
	if source.docstatus != 1 or source.is_return:
		frappe.throw(_("Select a submitted non-return Sales Invoice."))
	if not source.update_stock:
		delivery_notes = sorted({row.delivery_note for row in source.items if row.delivery_note})
		if not delivery_notes:
			frappe.throw(
				_("This Sales Invoice is not linked to a Delivery Note. Select the Delivery Note instead.")
			)
		return {
			"source_type": "Delivery Note",
			"delivery_note": delivery_notes[0] if len(delivery_notes) == 1 else None,
			"delivery_notes": delivery_notes,
			"items": [],
		}

	items = []
	for row in source.items:
		remaining = _round_return_request_qty(
			get_remaining_returnable_sales_invoice_quantity(row.name, exclude_request)
		)
		if remaining <= 0:
			continue
		items.append(
			{
				"sales_invoice_item": row.name,
				"item_code": row.item_code,
				"item_name": row.item_name,
				"delivered_qty": abs(flt(row.qty)),
				"requested_return_qty": remaining,
				"uom": row.uom,
				"stock_uom": row.stock_uom,
				"conversion_factor": flt(row.conversion_factor) or 1,
			}
		)
	return {"source_type": "Sales Invoice", "delivery_note": None, "items": items}


@frappe.whitelist()
def create_return_request_from_delivery_note(delivery_note):
	"""Create a prefilled draft for a later customer return."""
	require_doctype_permission("Return Request", "create")
	source = frappe.get_doc("Delivery Note", delivery_note)
	require_document_permission(source, "read")
	require_company_access(source.company)
	if source.docstatus != 1 or source.is_return:
		frappe.throw(_("Select a submitted original Delivery Note."))
	items = get_returnable_items(source.name)
	if not items:
		frappe.throw(_("Delivery Note {0} has no remaining returnable quantity.").format(source.name))

	request = frappe.new_doc("Return Request")
	request.update(
		{
			"company": source.company,
			"customer": source.customer,
			"delivery_note": source.name,
			"delivery_address": source.shipping_address_name,
			"return_source": "Customer Return",
			"initiated_by": "Customer",
			"return_warehouse": _default_return_warehouse(source.name),
		}
	)
	for row in items:
		request.append("items", row)
	request.insert()
	return request.name


@frappe.whitelist()
def transition_return_request(return_request, action):
	"""Retain only explicit terminal actions; operational stages are derived."""
	request = frappe.get_doc("Return Request", return_request)
	require_document_permission(request, "write")
	if action == "submit" and request.docstatus == 0:
		request.submit()
	elif action == "close" and request.docstatus == 1:
		state = get_return_request_state(request)
		if state["status"] != "Resolved":
			frappe.throw(_("Resolve the commercial outcome before closing this Return Request."))
		request.db_set("status", "Closed", update_modified=True)
	elif action == "cancel" and request.docstatus == 1:
		request.cancel()
	else:
		frappe.throw(_("Unsupported Return Request action."))
	return request.name


@frappe.whitelist()
def record_custody_transfer(return_request, to_custody, responsible_party=None, remarks=None):
	"""Record a custody handover without allowing the history to be edited in the browser."""
	request = frappe.get_doc("Return Request", return_request)
	require_document_permission(request, "write")
	require_company_access(request.company)
	if request.status in TERMINAL_STATUSES:
		frappe.throw(
			_("Custody cannot change after Return Request {0} is {1}.").format(request.name, request.status)
		)
	if not to_custody or to_custody == "Unknown":
		frappe.throw(_("Select the custody now responsible for the goods."))
	if not responsible_party or responsible_party == "Unknown":
		frappe.throw(_("Select the party responsible for the goods."))
	from_custody = request.custody
	request.custody = to_custody
	request.responsible_party = responsible_party
	request.append_custody_event(from_custody, to_custody, responsible_party, remarks)
	if request.docstatus == 1:
		request.flags.ignore_validate_update_after_submit = True
	request.save()
	return request.name


@frappe.whitelist()
def create_return_stock_document(return_request):
	"""Prepare the appropriate ERPNext stock return for the request source."""
	frappe.db.sql("select name from `tabReturn Request` where name = %s for update", return_request)
	request = frappe.get_doc("Return Request", return_request)
	require_document_permission(request, "read")
	require_company_access(request.company)
	if request.docstatus != 1:
		frappe.throw(_("Submit the Return Request before creating its Sales Return."))
	if not request.delivery_note:
		require_doctype_permission("Sales Invoice", "create")
		if request.credit_note:
			linked_status = frappe.db.get_value("Sales Invoice", request.credit_note, "docstatus")
			if linked_status is not None and linked_status != 2:
				return {"doctype": "Sales Invoice", "name": request.credit_note}
		from erpnext.controllers.sales_and_purchase_return import make_return_doc

		source = frappe.get_doc("Sales Invoice", request.sales_invoice)
		require_document_permission(source, "read")
		target = make_return_doc("Sales Invoice", source.name)
		requested_by_item = {row.sales_invoice_item: row for row in request.items}
		target_items = []
		for row in target.items:
			request_item = requested_by_item.get(row.sales_invoice_item)
			if not request_item:
				continue
			quantity = _round_return_request_qty(request_item.requested_return_qty)
			if quantity <= 0:
				continue
			row.qty = -quantity
			row.stock_qty = -flt(quantity * (flt(row.conversion_factor) or 1), row.precision("stock_qty"))
			row.warehouse = request.return_warehouse
			target_items.append(row)
		target.set("items", target_items)
		if not target.items:
			frappe.throw(_("No return items could be mapped to the original Sales Invoice."))
		target.insert()
		request.db_set("credit_note", target.name, update_modified=True)
		recalculate_return_request(request.name)
		return {"doctype": "Sales Invoice", "name": target.name}

	require_doctype_permission("Delivery Note", "create")
	if request.return_stock_document:
		linked_status = frappe.db.get_value("Delivery Note", request.return_stock_document, "docstatus")
		if linked_status is not None and linked_status != 2:
			return {"doctype": "Delivery Note", "name": request.return_stock_document}
	from erpnext.controllers.sales_and_purchase_return import make_return_doc

	source = frappe.get_doc("Delivery Note", request.delivery_note)
	require_document_permission(source, "read")
	target = make_return_doc("Delivery Note", source.name)
	requested_by_item = {row.delivery_note_item: row for row in request.items}
	target_items = []
	for row in target.items:
		request_item = requested_by_item.get(row.dn_detail)
		if not request_item:
			continue
		conversion_factor = flt(row.conversion_factor) or 1
		expected_qty = _round_return_request_qty(request_item.requested_return_qty)
		if expected_qty <= 0:
			continue
		row.qty = -expected_qty
		row.stock_qty = -flt(expected_qty * conversion_factor, row.precision("stock_qty"))
		row.warehouse = request.return_warehouse
		target_items.append(row)
	target.set("items", target_items)
	if not target.items:
		frappe.throw(_("No return items could be mapped to the original Delivery Note."))
	target.vuna_return_request = request.name
	target.insert()
	request.db_set("return_stock_document", target.name, update_modified=True)
	recalculate_return_request(request.name)
	return {"doctype": "Delivery Note", "name": target.name}


@frappe.whitelist()
def record_no_further_action(return_request, remarks):
	request = frappe.get_doc("Return Request", return_request)
	require_document_permission(request, "write")
	require_company_access(request.company)
	state = get_return_request_state(request)
	if state["return_docstatus"] != 1:
		frappe.throw(_("Submit the Sales Return before resolving the commercial outcome."))
	if not remarks:
		frappe.throw(_("Remarks are required for No Further Action."))
	values = {
		"commercial_resolution": "No Further Action",
		"no_further_action": 1,
		"no_further_action_remarks": remarks,
		"resolved_by": frappe.session.user,
		"resolved_on": now_datetime(),
	}
	frappe.db.set_value("Return Request", request.name, values, update_modified=True)
	recalculate_return_request(request.name)
	return request.name


@frappe.whitelist()
def create_replacement_sales_order(return_request):
	"""Prepare a draft replacement Sales Order for stock that was actually returned."""
	require_doctype_permission("Sales Order", "create")
	frappe.db.sql("select name from `tabReturn Request` where name = %s for update", return_request)
	request = frappe.get_doc("Return Request", return_request)
	require_document_permission(request, "write")
	require_company_access(request.company)
	state = get_return_request_state(request)
	if state["return_docstatus"] != 1:
		frappe.throw(_("Submit the Sales Return before creating a replacement."))
	if not request.delivery_note:
		frappe.throw(
			_("Replacement Sales Orders are available only for returns originating from a Delivery Note.")
		)
	if request.replacement_sales_order:
		linked_status = frappe.db.get_value("Sales Order", request.replacement_sales_order, "docstatus")
		if linked_status is not None and linked_status != 2:
			return request.replacement_sales_order
	if not request.sales_order:
		frappe.throw(_("The original Sales Order is required to prepare a replacement."))

	source = frappe.get_doc("Sales Order", request.sales_order)
	require_document_permission(source, "read")
	delivery_items = {
		row.name: row
		for row in frappe.get_all(
			"Delivery Note Item",
			filters={"parent": request.delivery_note},
			fields=["name", "so_detail", "item_code"],
		)
	}
	returned_by_sales_order_item = defaultdict(float)
	for delivery_note_item, returned_qty in state["returned_stock_by_item"].items():
		delivery_item = delivery_items.get(delivery_note_item)
		if delivery_item and delivery_item.so_detail:
			returned_by_sales_order_item[delivery_item.so_detail] += returned_qty

	target = frappe.new_doc("Sales Order")
	target.update(
		{
			"company": request.company,
			"customer": request.customer,
			"transaction_date": nowdate(),
			"delivery_date": add_days(nowdate(), 1),
			"order_type": source.order_type,
			"currency": source.currency,
			"conversion_rate": source.conversion_rate,
			"selling_price_list": source.selling_price_list,
			"price_list_currency": source.price_list_currency,
			"plc_conversion_rate": source.plc_conversion_rate,
		}
	)
	for source_item in source.items:
		stock_qty = returned_by_sales_order_item.get(source_item.name, 0)
		if stock_qty <= 0:
			continue
		conversion_factor = flt(source_item.conversion_factor) or 1
		target.append(
			"items",
			{
				"item_code": source_item.item_code,
				"item_name": source_item.item_name,
				"description": source_item.description,
				"qty": stock_qty / conversion_factor,
				"uom": source_item.uom,
				"conversion_factor": conversion_factor,
				"stock_uom": source_item.stock_uom,
				"warehouse": source_item.warehouse,
				"rate": source_item.rate,
				"delivery_date": add_days(nowdate(), 1),
			},
		)
	if not target.items:
		frappe.throw(_("No returned quantity could be mapped to the original Sales Order."))
	target.insert()
	frappe.db.set_value(
		"Return Request",
		request.name,
		{
			"commercial_resolution": "Replacement",
			"replacement_sales_order": target.name,
		},
		update_modified=True,
	)
	recalculate_return_request(request.name)
	return target.name


def validate_linked_sales_return_submission(doc, method=None):
	"""Keep a tagged Sales Return within its requested quantity without replacing ERPNext's flow."""
	if not doc.is_return:
		return
	return_request = _get_linked_return_request_name(doc, submitted_only=True)
	if not return_request:
		return
	request = frappe.get_doc("Return Request", return_request)
	require_company_access(request.company)
	_map_split_sales_return_rows(request, doc)
	outcomes = _get_sales_return_outcomes(request, doc)
	request_items = {row.delivery_note_item: row for row in request.items}
	for delivery_note_item, outcome in outcomes.items():
		requested = flt(request_items[delivery_note_item].requested_return_qty)
		if outcome["returned"] > requested:
			frappe.throw(
				_("Sales Return quantity for {0} cannot exceed the requested return quantity {1}.").format(
					request_items[delivery_note_item].item_code, requested
				)
			)


def validate_linked_sales_invoice_credit_note_submission(doc, method=None):
	"""Keep a direct stock-invoice credit note within its Return Request quantity."""
	if not doc.is_return:
		return
	return_request = frappe.db.get_value("Return Request", {"credit_note": doc.name, "docstatus": 1}, "name")
	if not return_request:
		return
	request = frappe.get_doc("Return Request", return_request)
	if request.delivery_note:
		return
	require_company_access(request.company)
	_map_split_credit_note_rows(request, doc)
	request_items = {row.sales_invoice_item: row for row in request.items}
	returned_by_item = defaultdict(float)
	for row in doc.items:
		if row.sales_invoice_item not in request_items:
			frappe.throw(
				_("Row #{0}: Item {1} does not belong to Return Request {2}.").format(
					row.idx, row.item_code, request.name
				)
			)
		returned_by_item[row.sales_invoice_item] += abs(flt(row.qty))
	for sales_invoice_item, returned_qty in returned_by_item.items():
		requested = flt(request_items[sales_invoice_item].requested_return_qty)
		if returned_qty > requested:
			frappe.throw(
				_("Credit Note quantity for {0} cannot exceed the requested return quantity {1}.").format(
					request_items[sales_invoice_item].item_code, requested
				)
			)


def _map_split_sales_return_rows(request, sales_return):
	"""Restore source lineage and commercial terms on linked Sales Return rows.

	A user may split one returned item into separate rows for different return
	warehouses or inspection outcomes. ERPNext treats a newly added row as a new
	item selection and can therefore fetch today's Item Price. A tagged Return
	Request must instead retain the price and discount executed on the original
	Delivery Note, while still allowing the user to choose the physical outcome.
	"""
	request_items = {row.delivery_note_item: row for row in request.items}
	request_items_by_code = defaultdict(list)
	source_context_by_item = {}
	for row in request.items:
		request_items_by_code[row.item_code].append(row)

	for row in sales_return.items:
		request_item = request_items.get(row.dn_detail)
		if not request_item and row.dn_detail:
			frappe.throw(
				_("Row #{0}: Item {1} does not belong to Return Request {2}.").format(
					row.idx, row.item_code, request.name
				)
			)
		if not request_item:
			candidates = request_items_by_code.get(row.item_code, [])
			if len(candidates) != 1:
				frappe.throw(
					_("Row #{0}: Item {1} cannot be matched uniquely to a Return Request item.").format(
						row.idx, row.item_code
					)
				)
			request_item = candidates[0]
			row.dn_detail = request_item.delivery_note_item

		source = source_context_by_item.get(request_item.delivery_note_item)
		if source is None:
			source = frappe.db.get_value(
				"Delivery Note Item",
				request_item.delivery_note_item,
				["against_sales_order", "so_detail", *RETURN_SALES_ITEM_COMMERCIAL_FIELDS],
				as_dict=True,
			)
			source_context_by_item[request_item.delivery_note_item] = source
		if not source or not hasattr(source, "get"):
			continue

		row.against_sales_order = source.against_sales_order
		row.so_detail = source.so_detail
		for fieldname in RETURN_SALES_ITEM_COMMERCIAL_FIELDS:
			setattr(row, fieldname, source.get(fieldname))


def _map_split_credit_note_rows(request, credit_note):
	"""Keep direct Sales Invoice lineage and commercial terms when users split rows."""
	request_items = {row.sales_invoice_item: row for row in request.items}
	request_items_by_code = defaultdict(list)
	for row in request.items:
		request_items_by_code[row.item_code].append(row)

	for row in credit_note.items:
		request_item = request_items.get(row.sales_invoice_item)
		if not request_item and row.sales_invoice_item:
			frappe.throw(
				_("Row #{0}: Item {1} does not belong to Return Request {2}.").format(
					row.idx, row.item_code, request.name
				)
			)
		if not request_item:
			candidates = request_items_by_code.get(row.item_code, [])
			if len(candidates) != 1:
				frappe.throw(
					_("Row #{0}: Item {1} cannot be matched uniquely to a Return Request item.").format(
						row.idx, row.item_code
					)
				)
			request_item = candidates[0]
			row.sales_invoice_item = request_item.sales_invoice_item

		source = frappe.db.get_value(
			"Sales Invoice Item",
			request_item.sales_invoice_item,
			RETURN_SALES_ITEM_COMMERCIAL_FIELDS,
			as_dict=True,
		)
		if source:
			for fieldname in RETURN_SALES_ITEM_COMMERCIAL_FIELDS:
				setattr(row, fieldname, source.get(fieldname))


def validate_sales_return_request_link(doc, method=None):
	"""Validate the visible Sales Return tag without making child rows repeat it."""
	if not doc.is_return:
		doc.vuna_return_request = None
		if not doc.vuna_dispatch_status:
			doc.vuna_dispatch_status = "Ready for Loading"
		return
	doc.vuna_delivery_load = None
	doc.vuna_delivery_order = None
	doc.vuna_dispatch_status = None
	doc.vuna_dispatch_scan_count = 0
	if not doc.vuna_return_request:
		doc.vuna_return_request = frappe.db.get_value(
			"Return Request",
			{"return_stock_document": doc.name, "docstatus": ["!=", 2]},
			"name",
		)
		return

	request = frappe.get_doc("Return Request", doc.vuna_return_request)
	require_document_permission(request, "read")
	if request.docstatus != 1:
		frappe.throw(_("Return Request {0} must be submitted.").format(request.name))
	if request.company != doc.company or request.customer != doc.customer:
		frappe.throw(_("Return Request must belong to the same Company and Customer."))
	if request.delivery_note and doc.return_against and request.delivery_note != doc.return_against:
		frappe.throw(
			_("Return Request {0} belongs to Delivery Note {1}, not {2}.").format(
				request.name, request.delivery_note, doc.return_against
			)
		)
	if request.get("process_version") == "Custody and Inspection":
		from vuna_transport.services.execution.return_v2_service import (
			validate_sales_return_lineage,
		)

		validate_sales_return_lineage(doc, request)
	if (
		request.get("process_version") != "Custody and Inspection"
		and request.return_stock_document
		and request.return_stock_document != doc.name
	):
		linked_status = frappe.db.get_value("Delivery Note", request.return_stock_document, "docstatus")
		if linked_status != 2:
			frappe.throw(
				_("Return Request {0} is already linked to Sales Return {1}.").format(
					request.name, request.return_stock_document
				)
			)
	_map_split_sales_return_rows(request, doc)


def sync_sales_return_request_link(doc, method=None):
	"""Synchronize the visible Sales Return tag with Return Request's authoritative link."""
	if not doc.is_return:
		return
	linked_requests = frappe.get_all(
		"Return Request",
		filters={"return_stock_document": doc.name, "docstatus": ["!=", 2]},
		pluck="name",
	)
	for request_name in linked_requests:
		if request_name == doc.vuna_return_request:
			continue
		frappe.db.set_value(
			"Return Request", request_name, "return_stock_document", None, update_modified=False
		)
		recalculate_return_request(request_name)

	if not doc.vuna_return_request:
		return
	if (
		frappe.db.get_value("Return Request", doc.vuna_return_request, "process_version")
		== "Custody and Inspection"
	):
		return
	if frappe.db.get_value("Return Request", doc.vuna_return_request, "return_stock_document") != doc.name:
		frappe.db.set_value(
			"Return Request",
			doc.vuna_return_request,
			"return_stock_document",
			doc.name,
			update_modified=False,
		)
		recalculate_return_request(doc.vuna_return_request)


def prepare_sales_return_cancellation(doc, method=None):
	"""Clear the reverse link before Frappe performs cancel/delete link checks."""
	if not doc.is_return:
		return
	return_request = _get_linked_return_request_name(doc)
	if not return_request:
		return
	inspection = frappe.db.get_value(
		"Return Inspection", {"sales_return": doc.name, "docstatus": ["!=", 2]}, "name"
	)
	if inspection:
		frappe.throw(
			_("Cancel and delete Return Inspection {0} before cancelling this Sales Return.").format(
				inspection
			)
		)
	if frappe.db.get_value("Return Request", return_request, "return_stock_document") == doc.name:
		frappe.db.set_value(
			"Return Request", return_request, "return_stock_document", None, update_modified=False
		)
		recalculate_return_request(return_request)


def _get_linked_return_request_name(doc, submitted_only=False):
	if getattr(doc, "vuna_return_request", None):
		if (
			not submitted_only
			or frappe.db.get_value("Return Request", doc.vuna_return_request, "docstatus") == 1
		):
			return doc.vuna_return_request
	filters = {"return_stock_document": doc.name, "docstatus": 1 if submitted_only else ["!=", 2]}
	return frappe.db.get_value("Return Request", filters, "name")


def _get_sales_return_outcomes(request, sales_return):
	"""Classify standard Sales Return rows by their destination Warehouse."""
	request_items = {row.delivery_note_item: row for row in request.items}
	warehouses = {row.warehouse for row in sales_return.items if row.warehouse}
	rejected_warehouses = set()
	if warehouses:
		rejected_warehouses = set(
			frappe.get_all(
				"Warehouse",
				filters={"name": ["in", list(warehouses)], "is_rejected_warehouse": 1},
				pluck="name",
			)
		)
	outcomes = defaultdict(lambda: {"returned": 0.0, "accepted": 0.0, "rejected": 0.0})
	for row in sales_return.items:
		if row.dn_detail not in request_items:
			continue
		stock_quantity = abs(flt(getattr(row, "stock_qty", None) or row.qty))
		quantity = _from_stock_qty(stock_quantity, request_items[row.dn_detail])
		outcomes[row.dn_detail]["returned"] += quantity
		classification = "rejected" if row.warehouse in rejected_warehouses else "accepted"
		outcomes[row.dn_detail][classification] += quantity
	return outcomes


def validate_linked_replacement_sales_order(doc, method=None):
	return_request = frappe.db.get_value(
		"Return Request", {"replacement_sales_order": doc.name, "docstatus": 1}, "name"
	)
	if not return_request:
		return
	request = frappe.get_doc("Return Request", return_request)
	state = get_return_request_state(request)
	delivery_item_codes = {
		row.name: row.item_code
		for row in frappe.get_all(
			"Delivery Note Item",
			filters={"parent": request.delivery_note},
			fields=["name", "item_code"],
		)
	}
	allowed_by_item = defaultdict(float)
	for delivery_note_item, returned_qty in state["returned_stock_by_item"].items():
		allowed_by_item[delivery_item_codes.get(delivery_note_item)] += returned_qty
	ordered_by_item = defaultdict(float)
	for row in doc.items:
		ordered_by_item[row.item_code] += flt(row.stock_qty or row.qty)
	for item_code, quantity in ordered_by_item.items():
		if quantity > allowed_by_item.get(item_code, 0):
			frappe.throw(
				_("Replacement quantity for {0} cannot exceed returned quantity {1}.").format(
					item_code, allowed_by_item.get(item_code, 0)
				)
			)


def sync_return_request_from_sales_return(doc, method=None):
	if not doc.is_return:
		return
	return_request = _get_linked_return_request_name(doc)
	if return_request:
		request = frappe.get_doc("Return Request", return_request)
		if request.get("process_version") == "Custody and Inspection":
			recalculate_return_request(return_request)
			return
		if doc.docstatus == 2:
			for row in request.items:
				frappe.db.set_value(
					"Return Request Item",
					row.name,
					{
						"received_qty": 0,
						"accepted_return_qty": 0,
						"rejected_return_qty": 0,
						"remaining_qty": flt(row.requested_return_qty),
					},
					update_modified=False,
				)
		elif doc.docstatus == 1:
			outcomes = _get_sales_return_outcomes(request, doc)
			for row in request.items:
				outcome = outcomes.get(
					row.delivery_note_item,
					{"returned": 0, "accepted": 0, "rejected": 0},
				)
				frappe.db.set_value(
					"Return Request Item",
					row.name,
					{
						"received_qty": outcome["returned"],
						"accepted_return_qty": outcome["accepted"],
						"rejected_return_qty": outcome["rejected"],
						"remaining_qty": max(flt(row.requested_return_qty) - outcome["returned"], 0),
					},
					update_modified=False,
				)
		recalculate_return_request(return_request)


def _sync_direct_credit_note_outcomes(request, credit_note):
	"""Persist physical return outcomes for a credit note made from a stock invoice."""
	if credit_note.docstatus == 2:
		for row in request.items:
			frappe.db.set_value(
				"Return Request Item",
				row.name,
				{
					"received_qty": 0,
					"accepted_return_qty": 0,
					"rejected_return_qty": 0,
					"remaining_qty": flt(row.requested_return_qty),
				},
				update_modified=False,
			)
		return
	if credit_note.docstatus != 1:
		return
	request_items = {row.sales_invoice_item: row for row in request.items}
	warehouses = {row.warehouse for row in credit_note.items if row.warehouse}
	rejected_warehouses = set()
	if warehouses:
		rejected_warehouses = set(
			frappe.get_all(
				"Warehouse",
				filters={"name": ["in", list(warehouses)], "is_rejected_warehouse": 1},
				pluck="name",
			)
		)
	outcomes = defaultdict(lambda: {"returned": 0.0, "accepted": 0.0, "rejected": 0.0})
	for row in credit_note.items:
		request_item = request_items.get(row.sales_invoice_item)
		if not request_item:
			continue
		quantity = abs(flt(row.qty))
		outcomes[row.sales_invoice_item]["returned"] += quantity
		classification = "rejected" if row.warehouse in rejected_warehouses else "accepted"
		outcomes[row.sales_invoice_item][classification] += quantity
	for row in request.items:
		outcome = outcomes[row.sales_invoice_item]
		frappe.db.set_value(
			"Return Request Item",
			row.name,
			{
				"received_qty": outcome["returned"],
				"accepted_return_qty": outcome["accepted"],
				"rejected_return_qty": outcome["rejected"],
				"remaining_qty": max(flt(row.requested_return_qty) - outcome["returned"], 0),
			},
			update_modified=False,
		)


def sync_return_request_from_quality_inspection(doc, method=None):
	request_item = _get_return_request_item_for_quality_inspection(doc)
	return_request = request_item.parent if request_item else None
	if return_request:
		if doc.docstatus == 2:
			frappe.db.set_value(
				"Return Request Item",
				request_item.name,
				"quality_inspection",
				None,
				update_modified=False,
			)
		recalculate_return_request(return_request)


def prepare_quality_inspection_removal(doc, method=None):
	"""Release the Return Request backlink before Frappe checks cancel/delete links."""
	request_item = _get_return_request_item_for_quality_inspection(doc)
	if not request_item:
		return
	frappe.db.set_value(
		"Return Request Item",
		request_item.name,
		"quality_inspection",
		None,
		update_modified=False,
	)
	recalculate_return_request(request_item.parent)


def _get_return_request_item_for_quality_inspection(doc):
	request_item = frappe.db.get_value(
		"Return Request Item", {"quality_inspection": doc.name}, ["name", "parent"], as_dict=True
	)
	if request_item or doc.reference_type not in {"Delivery Note", "Sales Invoice"} or not doc.reference_name:
		return request_item

	link_field = "return_stock_document" if doc.reference_type == "Delivery Note" else "credit_note"
	return_request = frappe.db.get_value(
		"Return Request", {link_field: doc.reference_name, "docstatus": ["!=", 2]}, "name"
	)
	if not return_request or not doc.child_row_reference:
		return None
	if doc.reference_type == "Delivery Note":
		source_item = frappe.db.get_value("Delivery Note Item", doc.child_row_reference, "dn_detail")
		source_field = "delivery_note_item"
	else:
		source_item = frappe.db.get_value("Sales Invoice Item", doc.child_row_reference, "sales_invoice_item")
		source_field = "sales_invoice_item"
	if not source_item:
		return None
	return frappe.db.get_value(
		"Return Request Item",
		{"parent": return_request, source_field: source_item},
		["name", "parent"],
		as_dict=True,
	)


def sync_return_request_from_commercial_document(doc, method=None):
	if doc.doctype == "Sales Invoice" and getattr(doc, "is_return", False):
		return_request = _get_return_request_from_sales_return_credit_note(doc)
		if return_request:
			if not any(getattr(row, "delivery_note", None) for row in doc.items):
				request = frappe.get_doc("Return Request", return_request)
				_sync_direct_credit_note_outcomes(request, doc)
			values = {"credit_note": doc.name if doc.docstatus == 1 else None}
			if doc.docstatus == 1:
				values["commercial_resolution"] = "Credit Customer"
			elif (
				frappe.db.get_value("Return Request", return_request, "commercial_resolution")
				== "Credit Customer"
			):
				values["commercial_resolution"] = "Pending"
			frappe.db.set_value("Return Request", return_request, values, update_modified=True)
			recalculate_return_request(return_request)
			return

	field_by_doctype = {
		"Sales Invoice": "credit_note",
		"Sales Order": "replacement_sales_order",
		"Payment Entry": "refund_payment_entry",
	}
	fieldname = field_by_doctype.get(doc.doctype)
	if not fieldname:
		return
	return_request = frappe.db.get_value(
		"Return Request", {fieldname: doc.name, "docstatus": ["!=", 2]}, "name"
	)
	if return_request:
		recalculate_return_request(return_request)


def _get_return_request_from_sales_return_credit_note(credit_note):
	"""Find the one Return Request represented by an ERPNext Credit Note's Sales Return rows."""
	direct_request = frappe.db.get_value(
		"Return Request", {"credit_note": credit_note.name, "docstatus": ["!=", 2]}, "name"
	)
	if direct_request:
		return direct_request
	sales_returns = {row.delivery_note for row in credit_note.items if getattr(row, "delivery_note", None)}
	if not sales_returns:
		return None

	filters = {
		"return_stock_document": ["in", list(sales_returns)],
		"docstatus": ["!=", 2],
	}
	if getattr(credit_note, "company", None):
		filters["company"] = credit_note.company
	if getattr(credit_note, "customer", None):
		filters["customer"] = credit_note.customer
	return_requests = frappe.get_all("Return Request", filters=filters, pluck="name")
	return return_requests[0] if len(return_requests) == 1 else None


@frappe.whitelist()
def create_return_request_from_pod(proof_of_delivery):
	"""Create one draft physical-return case for a non-accepted submitted POD.

	The request remains a draft. Users still review quantities and explicitly
	advance it before ERPNext stock or accounting documents are prepared.
	"""
	require_doctype_permission("Return Request", "create")
	frappe.db.sql("select name from `tabProof of Delivery` where name = %s for update", proof_of_delivery)
	pod = frappe.get_doc("Proof of Delivery", proof_of_delivery)
	require_document_permission(pod, "read")
	require_company_access(pod.company)
	if pod.docstatus != 1 or pod.status not in {"Partially Delivered", "Rejected", "Failed"}:
		frappe.throw(_("A Return Request requires a submitted non-accepted Proof of Delivery."))
	existing = frappe.db.get_value(
		"Return Request",
		{"proof_of_delivery": pod.name, "docstatus": ["!=", 2]},
		"name",
	)
	if existing:
		return existing
	delivery_note = frappe.get_doc("Delivery Note", pod.delivery_note)
	delivery_note_items = {row.name: row for row in delivery_note.items}
	request_rows = []
	for row in pod.items:
		delivery_note_item = delivery_note_items.get(row.delivery_note_item)
		if not delivery_note_item:
			frappe.throw(
				_("Delivery Note Item {0} could not be found in Delivery Note {1}.").format(
					row.delivery_note_item, delivery_note.name
				)
			)
		quantity = get_pod_quantity_in_sales_uom(row, delivery_note_item, "rejected_qty")
		if quantity <= 0:
			continue
		request_rows.append(
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"delivery_note_item": row.delivery_note_item,
				"delivery_order_item": row.delivery_order_item,
				"delivered_qty": get_pod_quantity_in_sales_uom(row, delivery_note_item, "delivered_qty"),
				"requested_return_qty": quantity,
				"uom": delivery_note_item.uom,
				"stock_uom": delivery_note_item.stock_uom,
				"conversion_factor": flt(delivery_note_item.conversion_factor) or 1,
				"batch_no": row.batch_no or get_single_delivery_note_item_batch(delivery_note_item),
				"return_reason": _get_pod_item_return_reason(pod, row),
			}
		)
	if not request_rows:
		frappe.throw(_("This Proof of Delivery has no rejected quantity for return."))

	request = frappe.new_doc("Return Request")
	request.update(
		{
			"company": pod.company,
			"customer": pod.customer,
			"sales_order": next(
				(row.against_sales_order for row in delivery_note.items if row.against_sales_order), None
			),
			"delivery_order": pod.delivery_order,
			"delivery_note": pod.delivery_note,
			"delivery_trip": pod.delivery_trip,
			"proof_of_delivery": pod.name,
			"delivery_address": pod.delivery_address,
			"return_warehouse": _default_return_warehouse(pod.delivery_note),
			"return_source": "Failed Delivery" if pod.status == "Failed" else "Delivery Rejection",
			"initiated_by": "Dispatcher",
			"return_method": "Driver Return",
			"return_remarks": pod.remarks or pod.notes,
		}
	)
	for row in request_rows:
		request.append("items", row)
	for reason in _get_pod_rejection_reasons(pod):
		request.append("return_reasons", {"rejection_reason": reason})
	request.insert()
	pod.db_set("return_request", request.name, update_modified=True)
	return request.name


def get_pod_quantity_in_stock_uom(pod_item, delivery_note_item, fieldname):
	"""Convert new POD quantities to stock UOM while preserving legacy POD semantics.

	POD items created before the sales-UOM snapshot was introduced stored stock
	quantities and therefore have no UOM. New POD items store Delivery Note `qty`
	in its transaction UOM and require the Delivery Note conversion factor here.
	"""
	quantity = flt(pod_item.get(fieldname) if hasattr(pod_item, "get") else getattr(pod_item, fieldname, 0))
	item_uom = pod_item.get("uom") if hasattr(pod_item, "get") else getattr(pod_item, "uom", None)
	if not item_uom:
		return quantity
	conversion_factor = flt(delivery_note_item.conversion_factor) or 1
	return quantity * conversion_factor


def get_pod_quantity_in_sales_uom(pod_item, delivery_note_item, fieldname):
	"""Return POD quantity in the Delivery Note UOM, including legacy POD rows."""
	quantity = flt(pod_item.get(fieldname) if hasattr(pod_item, "get") else getattr(pod_item, fieldname, 0))
	item_uom = pod_item.get("uom") if hasattr(pod_item, "get") else getattr(pod_item, "uom", None)
	if item_uom:
		return quantity
	return _from_stock_qty(quantity, delivery_note_item)


def _to_stock_qty(quantity, item):
	return flt(quantity) * (flt(getattr(item, "conversion_factor", None)) or 1)


def _from_stock_qty(quantity, item):
	return flt(quantity) / (flt(getattr(item, "conversion_factor", None)) or 1)


def _default_return_warehouse(delivery_note_name):
	"""Prefer ERPNext's company Sales Return warehouse, with the source as a draft fallback."""
	company = frappe.db.get_value("Delivery Note", delivery_note_name, "company")
	return frappe.db.get_value(
		"Company", company, "default_warehouse_for_sales_return"
	) or frappe.db.get_value("Delivery Note Item", {"parent": delivery_note_name}, "warehouse")


def _get_pod_rejection_reasons(pod):
	return list(
		dict.fromkeys(
			reason.rejection_reason
			for reason in (getattr(pod, "rejection_reasons", None) or [])
			if getattr(reason, "rejection_reason", None)
		)
	)


def _get_pod_item_return_reason(pod, item):
	"""Prefer the affected item's reason, then fall back to POD-level context."""
	if getattr(item, "rejection_reason", None):
		return item.rejection_reason
	if getattr(item, "rejection_reason_remarks", None):
		return item.rejection_reason_remarks
	return ", ".join(_get_pod_rejection_reasons(pod)) or pod.remarks or pod.notes


def _return_method_from_custody(custody):
	return {
		"Still With Vehicle": "Driver Return",
		"Held By Transporter": "Third-party Return",
		"Left With Customer": "Customer Drop-off",
	}.get(custody, "Company Pickup")
