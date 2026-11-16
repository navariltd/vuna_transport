"""POD-sized diversion receipts, custody evidence and reversal boundaries."""

import math

import frappe
from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
from frappe import _
from frappe.utils import flt, nowdate, nowtime

from vuna_transport.services.execution.depot_custody_service import (
	receipt_groups,
	rejection_custody_groups,
)
from vuna_transport.services.shared.security_service import require_document_permission


def planned_stock(doc):
	"""The immutable approved cargo, not an editable/partially received Stock Entry."""
	return frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"company": doc.company,
			"to_warehouse": doc.vehicle_warehouse,
			"items": [
				{
					"name": row.name,
					"item_code": row.item_code,
					"item_name": row.item_code,
					"qty": row.qty,
					"transfer_qty": row.stock_qty,
					"uom": row.uom,
					"stock_uom": row.stock_uom,
					"conversion_factor": row.conversion_factor,
					"serial_no": row.serial_no,
					"batch_no": row.batch_no,
					"serial_and_batch_bundle": None,
				}
				for row in doc.items
			],
		}
	)


def normalize_source_rows(proof, doc, entry):
	# Drafts downloaded before the exception implementation used the planned SE row ID.
	aliases = {row.name: row.get("vuna_diversion_item") for row in entry.items}
	approved = {row.name for row in doc.items}
	for row in proof.items:
		if row.source_row not in approved and aliases.get(row.source_row) in approved:
			row.source_row = aliases[row.source_row]


def submitted_proof(doc):
	if not doc.get("execution_task"):
		return None
	name = frappe.db.get_value(
		"Proof of Delivery", {"transport_trip_task": doc.execution_task, "docstatus": 1}, "name"
	)
	return frappe.get_doc("Proof of Delivery", name) if name else None


def canonical_source(doc, proof, source):
	if source in {row.name for row in doc.items}:
		return source
	# Preserve already submitted full-receipt proofs: their source was the stock-plan row.
	return frappe.db.get_value(
		"Stock Entry Detail",
		{"name": source, "parent": proof.get("dispatch_stock_entry")},
		"vuna_diversion_item",
	)


def expected_receipts(doc, proof):
	items = {row.name: row for row in doc.items}
	groups = {}
	for group in receipt_groups(proof):
		planned = items.get(canonical_source(doc, proof, group.result.source_row))
		if not planned:
			frappe.throw(_("The receipt proof does not identify an approved diversion item."))
		warehouse = doc.destination_warehouse if group.kind == "Accepted" else proof.rejected_goods_warehouse
		key = (planned.name, group.kind)
		if key in groups or not warehouse:
			frappe.throw(_("Keep distinct accepted and rejected receipt groups with known destinations."))
		groups[key] = frappe._dict(
			planned=planned,
			qty=group.qty,
			stock_qty=group.qty * planned.conversion_factor,
			warehouse=warehouse,
			serial_no=group.serial_no,
			proof_row=group.result,
		)
	return groups


def validate_receipt(entry, doc, proof):
	from vuna_transport.services.execution.shipment_diversion_documents import _identity

	if (
		proof.docstatus != 1
		or proof.transport_trip_task != doc.execution_task
		or entry.get("vuna_shipment_diversion") != doc.name
		or entry.company != doc.company
		or entry.purpose != "Material Transfer"
		or entry.add_to_transit
		or entry.outgoing_stock_entry
	):
		frappe.throw(_("The receipt must retain its approved diversion and submitted delivery proof."))
	expected = expected_receipts(doc, proof)
	seen = set()
	for row in entry.items:
		key = (row.get("vuna_diversion_item"), row.get("vuna_diversion_result"))
		# Full accepted receipts created before result tags existed remain identifiable.
		if not key[1] and (key[0], "Accepted") in expected:
			key = (key[0], "Accepted")
		group = expected.get(key)
		planned = group.planned if group else None
		if (
			not group
			or key in seen
			or row.item_code != planned.item_code
			or row.uom != planned.uom
			or row.stock_uom != planned.stock_uom
			or not math.isfinite(flt(row.qty))
			or abs(flt(row.qty) - group.qty) > 1e-6
			or not math.isfinite(flt(row.conversion_factor))
			or abs(flt(row.conversion_factor) - planned.conversion_factor) > 1e-6
			or not math.isfinite(flt(row.transfer_qty))
			or abs(flt(row.transfer_qty) - group.stock_qty) > 1e-6
			or row.s_warehouse != doc.vehicle_warehouse
			or row.t_warehouse != group.warehouse
		):
			frappe.throw(_("Stock rows must match the quantities and custody actually confirmed on the POD."))
		identity = _identity(row, row.transfer_qty)
		batch = planned.batch_no or ""
		if (
			set(identity) != {batch}
			or abs(flt(identity[batch]["stock_qty"]) - group.stock_qty) > 1e-6
			or set(identity[batch]["serials"]) != set(get_serial_nos(group.serial_no or ""))
		):
			frappe.throw(_("Receipt batches and serial numbers must match the actual POD handover."))
		if key[1] == "Rejected":
			request_name = proof.get("return_request")
			request_item = (
				frappe.db.get_value(
					"Return Request Item",
					{
						"parent": request_name,
						"source_pod_item": group.proof_row.name,
						"custody_warehouse": group.warehouse,
					},
					"name",
				)
				if request_name
				else None
			)
			if (
				not request_item
				or entry.get("vuna_return_request") != request_name
				or row.get("vuna_return_request_item") != request_item
			):
				frappe.throw(
					_("Rejected depot stock must retain its rejection Return Request and custody item link.")
				)
		elif row.get("vuna_return_request_item"):
			frappe.throw(_("Accepted goods must not count as returned rejected stock."))
		seen.add(key)
	if seen != set(expected) or not seen:
		frappe.throw(
			_("Receive every physically unloaded POD group once; do not receive goods left on the vehicle.")
		)


def make_bundle(entry, row, planned, quantity, serial_no):
	"""Fresh outward identity; never resize or reuse an already submitted bundle."""
	row.use_serial_batch_fields = 0
	row.serial_no = row.batch_no = row.serial_and_batch_bundle = None
	if not planned.batch_no and not serial_no:
		return
	serials = get_serial_nos(serial_no or "")
	entries = [{"serial_no": serial, "batch_no": planned.batch_no, "qty": -1} for serial in serials]
	if not entries:
		entries = [{"batch_no": planned.batch_no, "qty": -quantity}]
	bundle = frappe.get_doc(
		{
			"doctype": "Serial and Batch Bundle",
			"company": entry.company,
			"item_code": planned.item_code,
			"warehouse": row.s_warehouse,
			"voucher_type": "Stock Entry",
			"type_of_transaction": "Outward",
			"posting_date": entry.get("posting_date") or nowdate(),
			"posting_time": entry.get("posting_time") or nowtime(),
			"has_serial_no": bool(serials),
			"has_batch_no": bool(planned.batch_no),
			"entries": entries,
		}
	)
	bundle.insert(ignore_permissions=True)
	row.serial_and_batch_bundle = bundle.name


def apply_receipt(entry, doc, proof, request=None):
	groups = expected_receipts(doc, proof)
	if not groups:
		return False
	old = {row.get("vuna_diversion_item"): row for row in entry.items}
	rows = []
	for (item, kind), group in groups.items():
		source = old.pop(item, None)
		values = source.as_dict() if source else {}
		values.update(
			{
				"item_code": group.planned.item_code,
				"qty": group.qty,
				"uom": group.planned.uom,
				"stock_uom": group.planned.stock_uom,
				"conversion_factor": group.planned.conversion_factor,
				"transfer_qty": group.stock_qty,
				"s_warehouse": doc.vehicle_warehouse,
				"t_warehouse": group.warehouse,
				"vuna_diversion_item": item,
				"vuna_diversion_result": kind,
				"vuna_return_request_item": None,
			}
		)
		if request and kind == "Rejected":
			values["vuna_return_request_item"] = next(
				row.name
				for row in request.items
				if row.source_pod_item == group.proof_row.name and row.custody_warehouse == group.warehouse
			)
		rows.append((values, group))
	entry.set("items", [])
	for values, group in rows:
		row = entry.append("items", values)
		make_bundle(entry, row, group.planned, group.stock_qty, group.serial_no)
	if request:
		entry.vuna_return_request = request.name
	return True


def create_rejection_request(doc, proof):
	if not any(flt(row.rejected_qty) for row in proof.items):
		return None
	if name := frappe.db.get_value(
		"Return Request", {"proof_of_delivery": proof.name, "docstatus": ["!=", 2]}, "name"
	):
		return frappe.get_doc("Return Request", name)
	groups = rejection_custody_groups(proof, doc.vehicle_warehouse)
	warehouses = {group.warehouse for group in groups.values()}
	original = frappe.get_doc("Return Request", doc.return_request)
	request = frappe.new_doc("Return Request")
	request.update(
		{
			"return_context": "Depot",
			"shipment_diversion": doc.name,
			"company": doc.company,
			"proof_of_delivery": proof.name,
			"source_stock_entry": proof.dispatch_stock_entry,
			"delivery_trip": doc.delivery_trip,
			"rejecting_warehouse": doc.destination_warehouse,
			"source_warehouse": next(iter(warehouses)) if len(warehouses) == 1 else None,
			"return_warehouse": proof.rejected_goods_warehouse or original.return_warehouse,
			"return_source": "Depot Rejection",
			"initiated_by": "Driver",
			"delivery_method": "Return Collection",
			"custody": "Still With Vehicle"
			if warehouses == {doc.vehicle_warehouse}
			else "Split Custody"
			if len(warehouses) > 1
			else "Returned To Depot",
			"status": "Draft",
			"return_remarks": proof.remarks,
		}
	)
	for group in groups.values():
		row = group.result
		request.append(
			"items",
			{
				"source_pod_item": row.name,
				"item_code": row.item_code,
				"item_name": row.item_name,
				"requested_return_qty": group.qty,
				"custody_warehouse": group.warehouse,
				"delivered_qty": row.delivered_qty,
				"uom": row.uom,
				"stock_uom": row.stock_uom,
				"conversion_factor": row.conversion_factor,
				"batch_no": row.batch_no,
				"serial_no": group.serial_no,
				"source_bundle": row.source_bundle,
				"return_reason": row.rejection_reason_remarks or row.rejection_reason or proof.remarks,
			},
		)
	request.insert(ignore_permissions=True)
	proof.db_set("return_request", request.name, update_modified=False)
	return request


def validate_rejection_request(request):
	from vuna_transport.services.execution.depot_return_service import depot_return_state
	from vuna_transport.services.shared.security_service import require_document_permission

	doc = frappe.get_doc("Shipment Diversion", request.shipment_diversion)
	proof = frappe.get_doc("Proof of Delivery", request.proof_of_delivery)
	if not request.flags.ignore_permissions:
		require_document_permission(doc, "read")
		require_document_permission(proof, "read")
	if (
		doc.docstatus != 1
		or doc.destination_type != "Depot"
		or proof.docstatus != 1
		or proof.transport_trip_task != doc.execution_task
		or proof.dispatch_stock_entry != request.source_stock_entry
		or proof.company != doc.company
		or request.company != doc.company
		or request.customer
		or request.delivery_note
		or request.sales_invoice
		or request.credit_note
		or request.return_stock_document
	):
		frappe.throw(
			_(
				"Depot diversion returns must retain their submitted POD, company and stock authority, without customer credit documents."
			)
		)
	if frappe.db.exists(
		"Return Request",
		{"proof_of_delivery": proof.name, "docstatus": ["!=", 2], "name": ["!=", request.name or ""]},
	):
		frappe.throw(_("This diversion proof already has a Return Request."))
	expected = rejection_custody_groups(proof, doc.vehicle_warehouse)
	warehouses = {group.warehouse for group in expected.values()}
	request.source_warehouse = next(iter(warehouses)) if len(warehouses) == 1 else None
	request.custody = (
		"Still With Vehicle"
		if warehouses == {doc.vehicle_warehouse}
		else "Split Custody"
		if len(warehouses) > 1
		else "Returned To Depot"
	)
	seen = set()
	for row in request.items:
		key = (row.source_pod_item, row.custody_warehouse)
		group = expected.get(key)
		if (
			not group
			or key in seen
			or not math.isfinite(flt(row.requested_return_qty))
			or abs(flt(row.requested_return_qty) - group.qty) > 1e-6
		):
			frappe.throw(_("Preserve every rejected POD custody group and its exact quantity."))
		for field in (
			"item_code",
			"item_name",
			"uom",
			"stock_uom",
			"conversion_factor",
			"batch_no",
			"source_bundle",
		):
			row.set(field, group.result.get(field))
		row.serial_no = group.serial_no
		seen.add(key)
	if seen != set(expected):
		frappe.throw(_("Keep all rejected groups in the diversion Return Request."))
	warehouse = frappe.get_doc("Warehouse", request.return_warehouse)
	if (
		warehouse.company != doc.company
		or warehouse.disabled
		or warehouse.is_group
		or warehouse.warehouse_type == "Transit"
	):
		frappe.throw(_("Choose an active receiving returns warehouse in this company, not vehicle transit."))
	request.return_context, request.return_source = "Depot", "Depot Rejection"
	request.rejecting_warehouse = doc.destination_warehouse
	request.initiation_date = request.initiation_date or nowdate()
	state = depot_return_state(request)
	for row in request.items:
		row.received_qty = state["returned_by_item"].get(row.name, 0)
		row.diverted_qty = state.get("diverted_by_item", {}).get(row.name, 0)
		row.remaining_qty = max(flt(row.requested_return_qty) - row.received_qty - row.diverted_qty, 0)
	request.diverted_qty = state.get("diverted_qty", 0)
	for field in ("status", "expected_return_qty", "returned_qty", "return_difference_qty"):
		request.set(field, state[field])


def guard_proof_reversal(proof):
	if frappe.db.exists(
		"Shipment Diversion", {"previous_proof_of_delivery": proof.name, "docstatus": ["!=", 2]}
	):
		frappe.throw(_("Cancel the follow-up diversions before reversing their source POD."))
	if frappe.db.exists("Return Request", {"proof_of_delivery": proof.name, "docstatus": ["!=", 2]}):
		frappe.throw(_("Cancel the rejection Return Request before reversing this POD."))
	if frappe.db.get_value("Stock Entry", proof.dispatch_stock_entry, "docstatus") == 1:
		frappe.throw(_("Cancel the posted depot receipt before reversing its POD."))


def prepare_receipt_reversal(entry):
	if not entry.get("vuna_shipment_diversion"):
		return
	doc = frappe.get_doc("Shipment Diversion", entry.vuna_shipment_diversion, for_update=True)
	references = frappe.get_all(
		"Proof of Delivery",
		filters={"dispatch_stock_entry": entry.name, "docstatus": 1},
		fields=["name", "transport_trip_task", "delivery_trip", "proof_type"],
	)
	if any(
		not doc.get("execution_task")
		or row.transport_trip_task != doc.execution_task
		or row.delivery_trip != doc.delivery_trip
		or row.proof_type != "Transfer Receipt"
		for row in references
	):
		frappe.throw(_("Other submitted proof depends on this Stock Entry. Reconcile that evidence first."))
	# The POD's dispatch link names this same ordinary transfer, not an upstream loading entry.
	entry.ignore_linked_doctypes = list(
		set(entry.get("ignore_linked_doctypes") or []) | {"Proof of Delivery"}
	)


def require_no_followups(doc):
	if frappe.db.exists("Shipment Diversion", {"previous_diversion": doc.name, "docstatus": ["!=", 2]}):
		frappe.throw(
			_("Cancel the follow-up diversions before reversing their parent diversion or source stock.")
		)


def return_item_available(request, row, exclude=None):
	from vuna_transport.services.execution.shipment_diversion_documents import _identity

	qty = flt(row.requested_return_qty) * flt(row.conversion_factor)
	serials = set(get_serial_nos(row.serial_no or ""))
	for used in frappe.db.sql(
		"""select i.* from `tabStock Entry Detail` i
		join `tabStock Entry` s on s.name=i.parent where i.vuna_return_request_item=%s
		and s.docstatus in (0,1) and s.add_to_transit=0 and s.name!=%s for update""",
		(row.name, exclude or ""),
		as_dict=True,
	):
		qty -= flt(used.transfer_qty)
		for identity in _identity(used, used.transfer_qty).values():
			serials.difference_update(identity["serials"])
	# The same goods cannot be reserved for both another diversion and a warehouse return.
	if (
		row.custody_warehouse
		and frappe.db.get_value("Warehouse", row.custody_warehouse, "warehouse_type") == "Transit"
	):
		source = frappe.db.get_value("Proof of Delivery Item", row.source_pod_item, "source_row")
		for child in frappe.db.sql(
			"""select i.stock_qty, i.serial_no from `tabShipment Diversion Item` i
			join `tabShipment Diversion` d on d.name=i.parent where d.previous_proof_of_delivery=%s
			and d.docstatus in (0,1) and i.source_diversion_item=%s for update""",
			(request.proof_of_delivery, source),
			as_dict=True,
		):
			qty -= flt(child.stock_qty)
			serials.difference_update(get_serial_nos(child.serial_no or ""))
	return max(qty, 0), serials


@frappe.whitelist()
def create_return_receipt(return_request: str) -> str:
	from vuna_transport.services.shared.security_service import require_doctype_permission

	require_doctype_permission("Stock Entry", "create")
	request = frappe.get_doc("Return Request", return_request)
	require_document_permission(request, "read")
	frappe.db.get_value("Proof of Delivery", request.proof_of_delivery, "name", for_update=True)
	request = frappe.get_doc("Return Request", request.name, for_update=True)
	if request.docstatus != 1 or not request.get("shipment_diversion"):
		frappe.throw(
			_("Submit the diversion rejection Return Request before receiving its vehicle-held goods.")
		)
	validate_rejection_request(request)
	if name := frappe.db.get_value(
		"Stock Entry",
		{"vuna_return_request": request.name, "vuna_shipment_diversion": ["is", "not set"], "docstatus": 0},
		"name",
	):
		require_document_permission(frappe.get_doc("Stock Entry", name), "read")
		return name
	entry = frappe.new_doc("Stock Entry")
	entry.update(
		{
			"company": request.company,
			"purpose": "Material Transfer",
			"stock_entry_type": "Material Transfer",
			"vuna_return_request": request.name,
			"to_warehouse": request.return_warehouse,
			"add_to_transit": 0,
		}
	)
	for approved in request.items:
		if approved.custody_warehouse == request.return_warehouse:
			continue
		if frappe.db.get_value("Warehouse", approved.custody_warehouse, "warehouse_type") != "Transit":
			continue  # Depot-held goods use the existing approved depot pickup flow.
		qty, serials = return_item_available(request, approved)
		if qty <= 0:
			continue
		row = entry.append(
			"items",
			{
				"item_code": approved.item_code,
				"qty": qty / approved.conversion_factor,
				"uom": approved.uom,
				"stock_uom": approved.stock_uom,
				"conversion_factor": approved.conversion_factor,
				"transfer_qty": qty,
				"s_warehouse": approved.custody_warehouse,
				"t_warehouse": request.return_warehouse,
				"vuna_return_request_item": approved.name,
			},
		)
		make_bundle(entry, row, approved, qty, "\n".join(sorted(serials)))
	if not entry.items:
		frappe.throw(
			_(
				"No uncommitted vehicle-held return quantities remain. Goods already received or assigned to a follow-up cannot be received again."
			)
		)
	entry.insert()
	return entry.name


def validate_return_receipt(entry, request):
	from vuna_transport.services.execution.shipment_diversion_documents import _identity

	require_document_permission(request, "read")
	frappe.db.get_value("Proof of Delivery", request.proof_of_delivery, "name", for_update=True)
	frappe.db.get_value("Return Request", request.name, "name", for_update=True)
	if (
		request.docstatus != 1
		or entry.company != request.company
		or entry.purpose != "Material Transfer"
		or entry.add_to_transit
		or entry.outgoing_stock_entry
	):
		frappe.throw(
			_("Receive the submitted diversion return with a normal same-company Material Transfer.")
		)
	validate_rejection_request(request)
	approved = {row.name: row for row in request.items}
	seen = set()
	for row in entry.items:
		source = approved.get(row.vuna_return_request_item)
		if (
			not source
			or source.name in seen
			or row.item_code != source.item_code
			or row.s_warehouse != source.custody_warehouse
			or row.t_warehouse != request.return_warehouse
			or row.uom != source.uom
			or row.stock_uom != source.stock_uom
			or flt(row.conversion_factor) != flt(source.conversion_factor)
			or not math.isfinite(flt(row.qty))
			or flt(row.qty) <= 0
			or not math.isfinite(flt(row.transfer_qty))
			or abs(flt(row.transfer_qty) - flt(row.qty) * flt(source.conversion_factor)) > 1e-6
		):
			frappe.throw(
				_("Return receipts must preserve the approved item, UOM, custody and receiving warehouse.")
			)
		qty, serials = return_item_available(request, source, entry.name)
		identity = _identity(row, row.transfer_qty)
		batch = source.batch_no or ""
		if (
			flt(row.transfer_qty) > qty + 1e-6
			or set(identity) != {batch}
			or not set(identity[batch]["serials"]).issubset(serials)
			or not math.isfinite(flt(identity[batch]["stock_qty"]))
			or abs(flt(identity[batch]["stock_qty"]) - flt(row.transfer_qty)) > 1e-6
			or (source.serial_no and len(identity[batch]["serials"]) != flt(row.transfer_qty))
		):
			frappe.throw(
				_(
					"The return quantity or identity is already received, diverted, or outside the approved custody group."
				)
			)
		seen.add(source.name)
