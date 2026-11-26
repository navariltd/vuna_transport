"""Explicit draft documents for approved diversions; no trip changes or auto-posting."""

import math

import frappe
from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
from frappe import _
from frappe.utils import cint, flt, get_datetime, nowdate

from vuna_transport.services.execution.shipment_diversion_service import (
	_diversion_context_checks,
	_get_shipment_diversion_context,
	_lock_diversion_source,
	available_diversion_stock,
	diversion_reservations,
	validate_diversion_destination,
)
from vuna_transport.services.execution.transfer_proof_service import stock_identities
from vuna_transport.services.shared.security_service import (
	require_doctype_permission,
	require_document_permission,
)

DOCUMENT_FIELDS = {
	"Sales Return": ("Delivery Note", "sales_return"),
	"Sales Order": ("Sales Order", "sales_order"),
	"Delivery Note": ("Delivery Note", "onward_delivery_note"),
	"Stock Entry": ("Stock Entry", "stock_entry"),
}


def document_context(name):
	# Match the POD -> request locking order used for quantity reservations.
	preview = frappe.get_doc("Shipment Diversion", name)
	require_document_permission(preview, "write")
	_lock_diversion_source(preview.return_request)
	doc = frappe.get_doc("Shipment Diversion", name, for_update=True)
	if doc.docstatus != 1:
		frappe.throw(_("Approve the Shipment Diversion before preparing documents."))
	context = (
		_get_shipment_diversion_context(doc.return_request, for_update=True, allow_resolved=True)
		if doc.get("previous_diversion")
		else _get_shipment_diversion_context(doc.return_request, for_update=True)
	)
	_diversion_context_checks(context, bool(cint(doc.custody_confirmed)), submitting=True)
	for field in (
		"company",
		"delivery_note",
		"proof_of_delivery",
		"delivery_trip",
		"vehicle",
		"driver",
		"vehicle_warehouse",
	):
		if doc.get(field) != context[field]:
			frappe.throw(_("The approved vehicle or source context has changed. Review the diversion first."))
	validate_diversion_destination(doc, context["customer"])
	candidates = {row["return_request_item"]: row for row in context["items"]}
	reservations = diversion_reservations(doc.proof_of_delivery, doc.name)
	if doc.get("previous_diversion"):
		from vuna_transport.services.execution.shipment_diversion_followup import followup_candidates

		candidates, reservations = followup_candidates(doc, context)
	source = frappe.get_doc("Delivery Note", doc.delivery_note)
	source_items = {row.name: row for row in source.items}
	for row in doc.items:
		candidate = candidates.get(row.return_request_item)
		if not candidate or row.stock_qty > available_diversion_stock(candidate, reservations)[0] + 1e-6:
			frappe.throw(_("The approved rejected quantity is no longer available for this diversion."))
		original = source_items.get(row.delivery_note_item)
		if not original:
			frappe.throw(_("The original delivery item is missing."))
		group = _identity(original, abs(flt(original.stock_qty))).get(row.batch_no or "")
		if (
			not group
			or row.stock_qty > flt(group["stock_qty"]) + 1e-6
			or not set(get_serial_nos(row.serial_no or "")).issubset(set(group["serials"]))
		):
			frappe.throw(_("The approved batch and serials must come from the original delivered stock."))
	return doc


def existing_document(doc, kind):
	if kind == "Sales Return" and doc.get("previous_diversion"):
		from vuna_transport.services.execution.shipment_diversion_followup import root_diversion

		return existing_document(root_diversion(doc), kind)
	doctype, field = DOCUMENT_FIELDS[kind]
	filters = {"vuna_shipment_diversion": doc.name, "docstatus": ["!=", 2]}
	if doctype == "Delivery Note":
		filters["is_return"] = int(kind == "Sales Return")
	names = frappe.get_all(doctype, filters=filters, pluck="name", limit_page_length=2)
	if len(names) > 1:
		frappe.throw(
			_("More than one active {0} exists for this diversion. Reconcile them first.").format(kind)
		)
	name = names[0] if names else doc.get(field)
	if not name:
		return None
	target = frappe.get_doc(doctype, name)
	require_document_permission(target, "read")
	if target.get("vuna_shipment_diversion") != doc.name:
		if kind == "Sales Return":
			return None  # An existing ordinary return needs explicit, exact-match adoption.
		frappe.throw(_("The linked document does not belong to this diversion."))
	return target if target.docstatus != 2 else None


def _remember(doc, kind, target):
	doc.db_set(DOCUMENT_FIELDS[kind][1], target.name, update_modified=True)
	if kind == "Stock Entry" and doc.get("execution_task"):
		if frappe.db.exists(
			"Proof of Delivery", {"transport_trip_task": doc.execution_task, "docstatus": ["!=", 2]}
		):
			if (
				frappe.db.get_value("Transport Trip Task", doc.execution_task, "dispatch_stock_entry")
				!= target.name
			):
				frappe.throw(_("Cancel the existing proof before replacing its receipt Stock Entry."))
		else:
			frappe.db.set_value(
				"Transport Trip Task",
				doc.execution_task,
				"dispatch_stock_entry",
				target.name,
				update_modified=False,
			)
	return {"doctype": target.doctype, "name": target.name}


def _identity(row, stock_qty):
	values = frappe._dict(row.as_dict())
	values.transfer_qty = abs(stock_qty)
	return stock_identities(values)


def _check_identity(row, planned, stock_qty):
	groups = _identity(row, stock_qty)
	if len(groups) != 1:
		frappe.throw(_("Keep one approved batch group per diversion row."))
	batch, identity = next(iter(groups.items()))
	if (
		batch != (planned.batch_no or "")
		or abs(flt(identity["stock_qty"]) - planned.stock_qty) > 1e-6
		or not math.isfinite(flt(identity["stock_qty"]))
		or set(identity["serials"]) != set(get_serial_nos(planned.serial_no or ""))
	):
		frappe.throw(_("Batch and serial identity must match the approved diverted goods."))


def validate_rows(target, diversion):
	"""Prevent a draft edit from changing the approved quantity, UOM or source lot."""
	expected = {row.name: row for row in diversion.items}
	seen = set()
	for row in target.items:
		key = row.get("vuna_diversion_item")
		planned = expected.get(key)
		quantity = -flt(row.qty) if target.get("is_return") else flt(row.qty)
		if (
			not planned
			or key in seen
			or not math.isfinite(quantity)
			or abs(quantity - planned.qty) > 1e-6
			or row.item_code != planned.item_code
			or row.uom != planned.uom
			or row.stock_uom != planned.stock_uom
			or not math.isfinite(flt(row.conversion_factor))
			or abs(flt(row.conversion_factor) - planned.conversion_factor) > 1e-6
		):
			frappe.throw(_("Document rows must match the approved diversion items, quantities and UOMs."))
		seen.add(key)
		if target.doctype != "Sales Order":
			stock_qty = row.transfer_qty if target.doctype == "Stock Entry" else abs(flt(row.stock_qty))
			if not math.isfinite(flt(stock_qty)) or abs(flt(stock_qty) - planned.stock_qty) > 1e-6:
				frappe.throw(_("Stock quantities must match the approved diversion."))
			_check_identity(row, planned, stock_qty)
	if seen != set(expected):
		frappe.throw(_("Include every approved diversion row exactly once."))


def _bundle(target, row, planned, inward=False):
	from erpnext.stock.serial_batch_bundle import SerialBatchCreation

	row.serial_and_batch_bundle = row.serial_no = row.batch_no = None
	row.use_serial_batch_fields = 0
	if not planned.batch_no and not planned.serial_no:
		if frappe.get_cached_value("Item", planned.item_code, "has_batch_no"):
			frappe.throw(
				_("Select the rejected batch on the Return Request before diverting batch-tracked goods.")
			)
		return
	bundle = SerialBatchCreation(
		frappe._dict(
			item_code=planned.item_code,
			company=target.company,
			warehouse=target.get("set_warehouse") or target.get("from_warehouse"),
			type_of_transaction="Inward" if inward else "Outward",
			voucher_type=target.doctype,
			qty=planned.stock_qty if inward else -planned.stock_qty,
			do_not_submit=True,
		)
	).make_serial_and_batch_bundle(
		serial_nos=get_serial_nos(planned.serial_no or ""),
		batch_nos={planned.batch_no: planned.stock_qty} if planned.batch_no else None,
	)
	row.serial_and_batch_bundle = bundle.name


def posted_return(doc):
	receipt = existing_document(doc, "Sales Return")
	if not receipt or receipt.docstatus != 1:
		frappe.throw(
			_("Submit the diversion's Sales Return into vehicle stock before preparing the onward movement.")
		)
	if (
		not receipt.is_return
		or receipt.return_against != doc.delivery_note
		or receipt.company != doc.company
		or receipt.customer != doc.original_customer
		or any(row.warehouse != doc.vehicle_warehouse for row in receipt.items)
	):
		frappe.throw(
			_(
				"The Sales Return must receive these goods from the original customer into this vehicle warehouse."
			)
		)
	if doc.get("previous_diversion"):
		from vuna_transport.services.execution.shipment_diversion_followup import validate_shared_receipt

		validate_shared_receipt(doc, receipt)
	else:
		validate_rows(receipt, doc)
	return receipt


def check_vehicle_stock(doc):
	from erpnext.stock.doctype.batch.batch import get_batch_qty
	from erpnext.stock.utils import get_stock_balance

	totals = {}
	for row in doc.items:
		key = (row.item_code, row.batch_no or "")
		totals[key] = totals.get(key, 0) + row.stock_qty
		for serial in get_serial_nos(row.serial_no or ""):
			values = frappe.db.get_value(
				"Serial No", serial, ["item_code", "warehouse", "status"], as_dict=True
			)
			if (
				not values
				or values.item_code != row.item_code
				or values.warehouse != doc.vehicle_warehouse
				or values.status != "Active"
			):
				frappe.throw(_("Serial {0} is not available on the approved vehicle.").format(serial))
	item_totals = {}
	for (item, batch), quantity in totals.items():
		item_totals[item] = item_totals.get(item, 0) + quantity
		if (
			batch
			and flt(get_batch_qty(batch_no=batch, warehouse=doc.vehicle_warehouse, item_code=item)) + 1e-6
			< quantity
		):
			frappe.throw(_("The approved batch quantity is no longer available on this vehicle."))
	for item, quantity in item_totals.items():
		if flt(get_stock_balance(item, doc.vehicle_warehouse)) + 1e-6 < quantity:
			frappe.throw(
				_("The approved quantity of {0} is no longer in the vehicle warehouse.").format(item)
			)


def check_customer_quality(doc, receipt):
	for planned in doc.items:
		source = next(
			row
			for row in receipt.items
			if row.vuna_diversion_item == (planned.get("origin_diversion_item") or planned.name)
		)
		inspections = frappe.get_all(
			"Quality Inspection",
			filters={
				"reference_type": "Delivery Note",
				"reference_name": receipt.name,
				"child_row_reference": source.name,
				"item_code": planned.item_code,
				"docstatus": ["!=", 2],
			},
			pluck="name",
		)
		if not inspections:
			frappe.throw(
				_(
					"Record and submit an Accepted Quality Inspection for returned item {0} before customer redelivery."
				).format(planned.item_code)
			)
		for name in inspections:
			qi = frappe.get_doc("Quality Inspection", name)
			require_document_permission(qi, "read")
			if (
				qi.docstatus != 1
				or qi.status != "Accepted"
				or (planned.batch_no and qi.batch_no != planned.batch_no)
			):
				frappe.throw(_("Resolve draft or rejected Quality Inspections before customer redelivery."))
		if doc.get("previous_proof_of_delivery"):
			cutoff = frappe.db.get_value(
				"Proof of Delivery", doc.previous_proof_of_delivery, "delivery_datetime"
			)
			if not cutoff or not any(
				get_datetime(frappe.get_doc("Quality Inspection", name).creation) > get_datetime(cutoff)
				for name in inspections
			):
				frappe.throw(
					_(
						"Inspect these goods again after the latest depot rejection before customer redelivery."
					)
				)


@frappe.whitelist()
def create_diversion_document(shipment_diversion: str, document_type: str) -> dict:
	"""Create once, then reopen the same draft/posted document on retries."""
	if document_type not in DOCUMENT_FIELDS:
		frappe.throw(_("Choose Sales Return, Sales Order, Delivery Note or Stock Entry."))
	require_doctype_permission(DOCUMENT_FIELDS[document_type][0], "create")
	preview = frappe.get_doc("Shipment Diversion", shipment_diversion)
	require_document_permission(preview, "write")
	if preview.docstatus == 1 and (target := existing_document(preview, document_type)):
		return {"doctype": target.doctype, "name": target.name}
	doc = document_context(shipment_diversion)
	if document_type in ("Sales Order", "Delivery Note") and doc.destination_type != "Customer":
		frappe.throw(_("Sales documents require a customer destination."))
	if document_type == "Stock Entry" and doc.destination_type != "Depot":
		frappe.throw(_("A depot transfer requires a receiving depot destination."))
	if target := existing_document(doc, document_type):
		return _remember(doc, document_type, target)
	if document_type == "Sales Return":
		if target := _reuse_sales_return(doc):
			return _remember(doc, document_type, target)
		target = _make_sales_return(doc)
	else:
		receipt = posted_return(doc)
		check_vehicle_stock(doc)
		if document_type == "Sales Order":
			target = _make_sales_order(doc)
		elif document_type == "Delivery Note":
			check_customer_quality(doc, receipt)
			target = _make_delivery_note(doc)
		else:
			target = _make_stock_entry(doc)
	target.insert()
	return _remember(doc, document_type, target)


def _reuse_sales_return(doc):
	request = frappe.get_doc("Return Request", doc.return_request)
	names = frappe.get_all(
		"Delivery Note",
		filters={"vuna_return_request": request.name, "docstatus": ["!=", 2], "is_return": 1},
		pluck="name",
	)
	if request.get("return_stock_document") and request.return_stock_document not in names:
		names.append(request.return_stock_document)
	for name in names:
		target = frappe.get_doc("Delivery Note", name, for_update=True)
		if target.docstatus == 2 or target.get("vuna_shipment_diversion"):
			continue
		require_document_permission(target, "write")
		if (
			not target.is_return
			or target.return_against != doc.delivery_note
			or target.company != doc.company
			or target.customer != doc.original_customer
			or target.get("vuna_return_request") != request.name
			or any(row.warehouse != doc.vehicle_warehouse for row in target.items)
		):
			frappe.throw(
				_(
					"Reconcile the existing Sales Return's origin and receiving warehouse; it cannot be received again."
				)
			)
		planned = {row.delivery_note_item: row for row in doc.items}
		for row in target.items:
			if row.dn_detail not in planned:
				frappe.throw(
					_("The existing Sales Return does not exactly match this diversion. Reconcile it first.")
				)
			row.vuna_diversion_item = planned[row.dn_detail].name
		validate_rows(target, doc)
		# Adoption records traceability only. Never alter quantities, warehouses or posting status.
		target.db_set("vuna_shipment_diversion", doc.name, update_modified=True)
		for row in target.items:
			row.db_set("vuna_diversion_item", row.vuna_diversion_item, update_modified=False)
		return target
	return None


def _make_sales_return(doc):
	from erpnext.controllers.sales_and_purchase_return import make_return_doc

	# V2 supports separate partial receipts. Legacy requests have one authoritative return.
	request = frappe.get_doc("Return Request", doc.return_request)
	if request.get("process_version") != "Custody and Inspection":
		frappe.throw(_("Use the custody-and-inspection return workflow for shipment diversion."))
	if request.get("return_stock_document"):
		linked = frappe.get_doc("Delivery Note", request.return_stock_document)
		if linked.docstatus != 2:
			frappe.throw(
				_(
					"Reconcile the existing Sales Return before preparing a separate diversion receipt. Do not receive these goods twice."
				)
			)
	# An untagged return may already have received this request, even without its forward link.
	for name in frappe.get_all(
		"Delivery Note",
		filters={"vuna_return_request": request.name, "docstatus": ["!=", 2], "is_return": 1},
		pluck="name",
	):
		if not frappe.db.get_value("Delivery Note", name, "vuna_shipment_diversion"):
			frappe.throw(
				_("Reconcile existing Sales Return {0} before receiving diverted goods.").format(name)
			)
	target = make_return_doc("Delivery Note", doc.delivery_note)
	target.vuna_shipment_diversion, target.vuna_return_request = doc.name, doc.return_request
	target.set_warehouse = doc.vehicle_warehouse
	rows = {row.dn_detail: row for row in target.items}
	target.set("items", [])
	for planned in doc.items:
		row = rows.get(planned.delivery_note_item)
		from vuna_transport.services.execution.return_service import (
			RETURN_SALES_ITEM_COMMERCIAL_FIELDS,
			sales_return_stock_allowance,
		)
		from vuna_transport.services.execution.return_uom_service import apply_sales_return_uom

		original = frappe.db.get_value(
			"Delivery Note Item",
			planned.delivery_note_item,
			["name", "qty", "stock_qty", "conversion_factor"],
			as_dict=True,
		)
		if (
			not row
			or not original
			or sales_return_stock_allowance("Delivery Note", original) + 1e-6 < planned.stock_qty
		):
			frappe.throw(_("The original Delivery Note no longer has this quantity available to return."))
		apply_sales_return_uom(row, planned, RETURN_SALES_ITEM_COMMERCIAL_FIELDS)
		row.qty, row.stock_qty = -planned.qty, -planned.stock_qty
		row.warehouse, row.vuna_diversion_item = doc.vehicle_warehouse, planned.name
		row.uom, row.stock_uom, row.conversion_factor = (
			planned.uom,
			planned.stock_uom,
			planned.conversion_factor,
		)
		_bundle(target, row, planned, inward=True)
		target.append("items", row)
	target.calculate_taxes_and_totals()
	return target


def _make_sales_order(doc):
	target = frappe.new_doc("Sales Order")
	target.update(
		{
			"company": doc.company,
			"customer": doc.customer,
			"order_type": "Sales",
			"transaction_date": nowdate(),
			"delivery_date": nowdate(),
			"shipping_address_name": doc.delivery_address,
			"set_warehouse": doc.vehicle_warehouse,
			"vuna_shipment_diversion": doc.name,
		}
	)
	for planned in doc.items:
		target.append(
			"items",
			{
				"item_code": planned.item_code,
				"qty": planned.qty,
				"uom": planned.uom,
				"stock_uom": planned.stock_uom,
				"conversion_factor": planned.conversion_factor,
				"warehouse": doc.vehicle_warehouse,
				"delivery_date": nowdate(),
				"vuna_diversion_item": planned.name,
			},
		)
	# Resolve Customer B's own prices, taxes and terms; never copy Customer A's rates.
	target.run_method("set_missing_values")
	target.run_method("calculate_taxes_and_totals")
	return target


def _make_delivery_note(doc):
	from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

	order = existing_document(doc, "Sales Order")
	if not order or order.docstatus != 1 or order.status in ("Closed", "On Hold", "Cancelled"):
		frappe.throw(_("Review and submit the new customer's Sales Order before creating the Delivery Note."))
	validate_rows(order, doc)
	target = make_delivery_note(order.name)
	target.vuna_shipment_diversion, target.set_warehouse = doc.name, doc.vehicle_warehouse
	planned = {row.name: row for row in doc.items}
	order_items = {row.name: row.vuna_diversion_item for row in order.items}
	for row in target.items:
		row.vuna_diversion_item = order_items[row.so_detail]
		source = planned[row.vuna_diversion_item]
		row.qty, row.stock_qty = source.qty, source.stock_qty
		row.warehouse = doc.vehicle_warehouse
		_bundle(target, row, source)
	target.calculate_taxes_and_totals()
	return target


def _make_stock_entry(doc):
	from vuna_transport.services.execution.allocation_transfer_service import set_allocation_stock_defaults

	target = frappe.new_doc("Stock Entry")
	target.update(
		{
			"company": doc.company,
			"stock_entry_type": "Material Transfer",
			"purpose": "Material Transfer",
			"from_warehouse": doc.vehicle_warehouse,
			"to_warehouse": doc.destination_warehouse,
			"add_to_transit": 0,
			"vuna_shipment_diversion": doc.name,
		}
	)
	for planned in doc.items:
		row = target.append(
			"items",
			{
				"item_code": planned.item_code,
				"qty": planned.qty,
				"uom": planned.uom,
				"stock_uom": planned.stock_uom,
				"conversion_factor": planned.conversion_factor,
				"transfer_qty": planned.stock_qty,
				"s_warehouse": doc.vehicle_warehouse,
				"t_warehouse": doc.destination_warehouse,
				"vuna_diversion_item": planned.name,
			},
		)
		_bundle(target, row, planned)
	set_allocation_stock_defaults(target, doc)
	return target


def validate_diversion_document(target, method=None):
	if target.doctype == "Stock Entry" and frappe.flags.get("vuna_diversion_receipt_proof"):
		from vuna_transport.services.execution.shipment_diversion_execution import validate_delegated_receipt

		validate_delegated_receipt(target, frappe.flags.vuna_diversion_receipt_proof)
		return
	previous = target.get_doc_before_save()
	if previous and previous.get("vuna_shipment_diversion") != target.get("vuna_shipment_diversion"):
		frappe.throw(_("Shipment Diversion linkage cannot be changed on a saved document."))
	if not target.get("vuna_shipment_diversion"):
		return
	if target.doctype == "Stock Entry":
		from vuna_transport.services.execution.shipment_diversion_exceptions import submitted_proof

		snapshot = frappe.get_doc("Shipment Diversion", target.vuna_shipment_diversion)
		if proof := submitted_proof(snapshot):
			require_document_permission(snapshot, "write")
			from vuna_transport.services.execution.shipment_diversion_execution import (
				validate_delegated_receipt,
			)

			validate_delegated_receipt(target, proof.name)
			return
	doc = document_context(target.vuna_shipment_diversion)
	kind = "Sales Return" if target.doctype == "Delivery Note" and target.is_return else target.doctype
	existing = existing_document(doc, kind)
	if existing and existing.name != target.name:
		frappe.throw(_("An active {0} already exists for this diversion.").format(kind))
	if target.company != doc.company:
		frappe.throw(_("The diversion and document must use the same company."))
	validate_rows(target, doc)
	if kind == "Sales Return":
		if (
			target.customer != doc.original_customer
			or target.return_against != doc.delivery_note
			or target.get("vuna_return_request") != doc.return_request
		):
			frappe.throw(
				_("The Sales Return must reference the original customer, Delivery Note and Return Request.")
			)
		if any(
			row.warehouse != doc.vehicle_warehouse
			or row.dn_detail
			!= next(item.delivery_note_item for item in doc.items if item.name == row.vuna_diversion_item)
			for row in target.items
		):
			frappe.throw(_("Receive the approved original delivery rows into the vehicle warehouse."))
		return
	receipt = posted_return(doc)
	if target.doctype == "Stock Entry":
		if (
			doc.destination_type != "Depot"
			or target.purpose != "Material Transfer"
			or target.add_to_transit
			or target.outgoing_stock_entry
			or any(
				row.s_warehouse != doc.vehicle_warehouse or row.t_warehouse != doc.destination_warehouse
				for row in target.items
			)
		):
			frappe.throw(
				_(
					"Transfer diverted goods directly from this vehicle to the approved depot, without Add-to-Transit or End Transit."
				)
			)
	else:
		if (
			doc.destination_type != "Customer"
			or target.customer != doc.customer
			or target.shipping_address_name != doc.delivery_address
			or any(row.warehouse != doc.vehicle_warehouse for row in target.items)
		):
			frappe.throw(_("Use the approved new customer, delivery address and vehicle warehouse."))
		if kind == "Delivery Note":
			order = existing_document(doc, "Sales Order")
			if not order or order.docstatus != 1:
				frappe.throw(_("Submit the diversion Sales Order before delivery."))
			links = {row.name: row.vuna_diversion_item for row in order.items}
			if any(
				row.against_sales_order != order.name or links.get(row.so_detail) != row.vuna_diversion_item
				for row in target.items
			):
				frappe.throw(_("Delivery rows must reference the diversion's submitted Sales Order."))
			check_customer_quality(doc, receipt)
	if target.doctype != "Sales Order":
		check_vehicle_stock(doc)


def guard_diversion_cancellation(doc, method=None):
	from vuna_transport.services.execution.shipment_diversion_exceptions import require_no_followups
	from vuna_transport.services.execution.shipment_diversion_execution import guard_execution_removal

	require_no_followups(doc)
	guard_execution_removal(doc)
	for kind in DOCUMENT_FIELDS:
		if kind == "Sales Return" and doc.get("previous_diversion"):
			continue
		if existing_document(doc, kind):
			frappe.throw(_("Cancel and delete the active {0} before cancelling this diversion.").format(kind))


def prepare_document_removal(target, method=None):
	if not target.get("vuna_shipment_diversion"):
		return
	doc = frappe.get_doc("Shipment Diversion", target.vuna_shipment_diversion, for_update=True)
	require_document_permission(doc, "write")
	from vuna_transport.services.execution.shipment_diversion_exceptions import (
		prepare_receipt_reversal,
		require_no_followups,
	)

	require_no_followups(doc)
	if target.doctype == "Stock Entry":
		prepare_receipt_reversal(target)
	kind = "Sales Return" if target.doctype == "Delivery Note" and target.is_return else target.doctype
	dependents = (
		("Sales Order", "Delivery Note", "Stock Entry")
		if kind == "Sales Return"
		else (("Delivery Note",) if kind == "Sales Order" else ())
	)
	for dependent in dependents:
		if existing_document(doc, dependent):
			frappe.throw(
				_("Cancel and delete the onward {0} before reversing this document.").format(dependent)
			)
	field = DOCUMENT_FIELDS[kind][1]
	if doc.get(field) == target.name:
		doc.db_set(field, None, update_modified=False)


def guard_diversion_quality_removal(qi, method=None):
	if qi.reference_type != "Delivery Note":
		return
	name = frappe.db.get_value("Delivery Note", qi.reference_name, "vuna_shipment_diversion")
	if not name:
		return
	doc = frappe.get_doc("Shipment Diversion", name, for_update=True)
	if existing_document(doc, "Delivery Note"):
		frappe.throw(
			_("Cancel and delete the diversion's onward Delivery Note before removing its quality clearance.")
		)
	origin = frappe.db.get_value("Delivery Note Item", qi.child_row_reference, "vuna_diversion_item")
	if origin:
		for name in frappe.get_all(
			"Shipment Diversion",
			filters={"return_request": doc.return_request, "docstatus": 1, "destination_type": "Customer"},
			pluck="name",
		):
			child = frappe.get_doc("Shipment Diversion", name)
			if any(row.get("origin_diversion_item") == origin for row in child.items) and existing_document(
				child, "Delivery Note"
			):
				frappe.throw(
					_("Reverse the follow-up customer delivery before removing its quality clearance.")
				)
