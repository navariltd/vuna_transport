"""Operational return-request validation and ERPNext return integration."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, flt, now_datetime, nowdate

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
	"Awaiting Sales Return",
	"Awaiting Inspection",
	"Awaiting Return Submission",
	"Awaiting Commercial Resolution",
}
TERMINAL_STATUSES = {"Resolved", "Closed", "Cancelled"}
VALID_STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES


def validate_return_request(request):
	"""Validate the return against the submitted delivery and live return balance."""
	if not request.initiation_date:
		request.initiation_date = nowdate()
	if request.status not in VALID_STATUSES:
		frappe.throw(_("Invalid Return Request status: {0}").format(request.status))
	delivery_note = _get_source_delivery_note(request)
	_populate_request_context(request, delivery_note)
	_set_return_source(request)
	_validate_return_warehouse(request)
	_validate_items(request, delivery_note)
	_validate_follow_up_documents(request, delivery_note)
	_apply_return_state(request, get_return_request_state(request))


def _set_return_source(request):
	if request.proof_of_delivery:
		pod_status = frappe.db.get_value("Proof of Delivery", request.proof_of_delivery, "status")
		request.return_source = "Failed Delivery" if pod_status == "Failed" else "Delivery Rejection"
	elif not request.return_source:
		request.return_source = "Manual"


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
	if not request.sales_invoice:
		sales_invoices = _get_source_sales_invoices(delivery_note.name)
		if len(sales_invoices) == 1:
			request.sales_invoice = sales_invoices[0]
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


def _validate_return_warehouse(request):
	if not request.return_warehouse:
		if request.docstatus == 1:
			frappe.throw(_("Return Warehouse is required before submitting the Return Request."))
		return
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
		row.uom = delivery_item.uom
		row.stock_uom = delivery_item.stock_uom
		row.conversion_factor = flt(delivery_item.conversion_factor) or 1
		row.delivered_qty = abs(flt(delivery_item.qty))
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
		_validate_quality_inspection(row, request, delivery_note)


def _validate_quality_inspection(row, request, delivery_note):
	if not row.quality_inspection:
		return
	inspection = frappe.get_doc("Quality Inspection", row.quality_inspection)
	expected_reference = getattr(request, "return_stock_document", None)
	if not expected_reference:
		frappe.throw(_("Create the draft Sales Return before linking Quality Inspections."))
	if inspection.reference_type != "Delivery Note" or inspection.reference_name != expected_reference:
		frappe.throw(
			_("Quality Inspection {0} must reference Delivery Note {1}.").format(
				row.quality_inspection, expected_reference
			)
		)
	if inspection.item_code != row.item_code:
		frappe.throw(_("Quality Inspection {0} is for a different item.").format(row.quality_inspection))


def _validate_follow_up_documents(request, delivery_note):
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
		if follow_up.name == request.delivery_note:
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
	expected_by_item = {row.delivery_note_item: flt(row.requested_return_qty) for row in request.items}
	returned_by_item = dict.fromkeys(expected_by_item, 0.0)
	returned_stock_by_item = dict.fromkeys(expected_by_item, 0.0)
	request_items = {row.delivery_note_item: row for row in request.items}
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
	elif not request.return_stock_document or return_docstatus in (None, 2):
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
		row.received_qty = state["returned_by_item"].get(row.delivery_note_item, 0)
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
				"delivered_qty": abs(flt(row.qty)),
				"requested_return_qty": remaining,
				"uom": row.uom,
				"stock_uom": row.stock_uom,
				"conversion_factor": flt(row.conversion_factor) or 1,
			}
		)
	return items


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
	"""Prepare one idempotent draft ERPNext return Delivery Note."""
	require_doctype_permission("Delivery Note", "create")
	frappe.db.sql("select name from `tabReturn Request` where name = %s for update", return_request)
	request = frappe.get_doc("Return Request", return_request)
	require_document_permission(request, "read")
	require_company_access(request.company)
	if request.docstatus != 1:
		frappe.throw(_("Submit the Return Request before creating its Sales Return."))
	if request.return_stock_document:
		linked_status = frappe.db.get_value("Delivery Note", request.return_stock_document, "docstatus")
		if linked_status is not None and linked_status != 2:
			return request.return_stock_document
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
		expected_qty = flt(request_item.requested_return_qty)
		if expected_qty <= 0:
			continue
		row.qty = -expected_qty
		row.stock_qty = -(expected_qty * conversion_factor)
		row.warehouse = request.return_warehouse
		target_items.append(row)
	target.set("items", target_items)
	if not target.items:
		frappe.throw(_("No return items could be mapped to the original Delivery Note."))
	target.vuna_return_request = request.name
	target.insert()
	request.db_set("return_stock_document", target.name, update_modified=True)
	recalculate_return_request(request.name)
	return target.name


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
def create_return_credit_note(return_request):
	"""Prepare a draft ERPNext Credit Note for the quantity actually returned to stock."""
	require_doctype_permission("Sales Invoice", "create")
	frappe.db.sql("select name from `tabReturn Request` where name = %s for update", return_request)
	request = frappe.get_doc("Return Request", return_request)
	require_document_permission(request, "write")
	require_company_access(request.company)
	state = get_return_request_state(request)
	if state["return_docstatus"] != 1:
		frappe.throw(_("Submit the Sales Return before creating a Credit Note."))
	if request.credit_note:
		linked_status = frappe.db.get_value("Sales Invoice", request.credit_note, "docstatus")
		if linked_status is not None and linked_status != 2:
			return request.credit_note

	sales_invoices = (
		[request.sales_invoice]
		if request.sales_invoice
		else _get_source_sales_invoices(request.delivery_note)
	)
	if not sales_invoices:
		frappe.throw(
			_("No submitted Sales Invoice was found for Delivery Note {0}.").format(request.delivery_note)
		)
	if len(sales_invoices) > 1:
		frappe.throw(
			_(
				"Delivery Note {0} is billed by multiple Sales Invoices. Select the source Sales Invoice first."
			).format(request.delivery_note)
		)

	from erpnext.controllers.sales_and_purchase_return import make_return_doc

	source_invoice = frappe.get_doc("Sales Invoice", sales_invoices[0])
	require_document_permission(source_invoice, "read")
	target = make_return_doc("Sales Invoice", source_invoice.name)
	returned_stock_by_item = state["returned_stock_by_item"]
	target_items = []
	for row in target.items:
		returned_stock_qty = returned_stock_by_item.get(row.dn_detail, 0)
		if returned_stock_qty <= 0:
			continue
		conversion_factor = flt(row.conversion_factor) or 1
		row.qty = -(returned_stock_qty / conversion_factor)
		row.stock_qty = -returned_stock_qty
		target_items.append(row)
	target.set("items", target_items)
	if not target.items:
		frappe.throw(_("The source Sales Invoice has no rows matching the returned items."))
	target.insert()
	frappe.db.set_value(
		"Return Request",
		request.name,
		{
			"sales_invoice": sales_invoices[0],
			"commercial_resolution": "Credit Customer",
			"credit_note": target.name,
		},
		update_modified=True,
	)
	recalculate_return_request(request.name)
	return target.name


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


def _map_split_sales_return_rows(request, sales_return):
	"""Restore source-row lineage cleared when a standard return row is duplicated."""
	request_items = {row.delivery_note_item: row for row in request.items}
	request_items_by_code = defaultdict(list)
	for row in request.items:
		request_items_by_code[row.item_code].append(row)

	for row in sales_return.items:
		if row.dn_detail in request_items:
			continue
		if row.dn_detail:
			frappe.throw(
				_("Row #{0}: Item {1} does not belong to Return Request {2}.").format(
					row.idx, row.item_code, request.name
				)
			)
		candidates = request_items_by_code.get(row.item_code, [])
		if len(candidates) != 1:
			frappe.throw(
				_("Row #{0}: Item {1} cannot be matched uniquely to a Return Request item.").format(
					row.idx, row.item_code
				)
			)
		request_item = candidates[0]
		row.dn_detail = request_item.delivery_note_item
		source = frappe.db.get_value(
			"Delivery Note Item",
			request_item.delivery_note_item,
			["against_sales_order", "so_detail"],
			as_dict=True,
		)
		if source:
			row.against_sales_order = source.against_sales_order
			row.so_detail = source.so_detail


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
	if request.return_stock_document and request.return_stock_document != doc.name:
		linked_status = frappe.db.get_value("Delivery Note", request.return_stock_document, "docstatus")
		if linked_status != 2:
			frappe.throw(
				_("Return Request {0} is already linked to Sales Return {1}.").format(
					request.name, request.return_stock_document
				)
			)


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
	if request_item or doc.reference_type != "Delivery Note" or not doc.reference_name:
		return request_item

	return_request = frappe.db.get_value(
		"Return Request",
		{"return_stock_document": doc.reference_name, "docstatus": ["!=", 2]},
		"name",
	)
	if not return_request or not doc.child_row_reference:
		return None
	delivery_note_item = frappe.db.get_value("Delivery Note Item", doc.child_row_reference, "dn_detail")
	if not delivery_note_item:
		return None
	return frappe.db.get_value(
		"Return Request Item",
		{"parent": return_request, "delivery_note_item": delivery_note_item},
		["name", "parent"],
		as_dict=True,
	)


def sync_return_request_from_commercial_document(doc, method=None):
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
