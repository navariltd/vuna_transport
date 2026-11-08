"""Receive rejected cargo already aboard a vehicle without a second collection."""

import frappe
from frappe import _
from frappe.utils import flt

from vuna_transport.services.execution.transfer_proof_service import stock_identities
from vuna_transport.services.shared.security_service import (
	require_doctype_permission,
	require_document_permission,
)


@frappe.whitelist()
def create_vehicle_receipt(return_request: str) -> str:
	from vuna_transport.services.execution.return_v2_service import _validate_warehouse

	request = frappe.get_doc("Return Request", return_request)
	require_document_permission(request, "read")
	require_doctype_permission("Stock Entry", "create")
	frappe.db.get_value("Return Request", request.name, "name", for_update=True)
	if request.docstatus != 1 or request.return_context != "Depot" or not request.proof_of_delivery:
		frappe.throw(_("Use a submitted depot rejection request for goods already in vehicle transit."))
	_validate_warehouse(request.return_warehouse, request.company)
	if "nl_vms" in frappe.get_installed_apps() and frappe.db.get_value(
		"Warehouse", request.return_warehouse, "is_rejected_warehouse"
	):
		frappe.throw(
			_("Select an ordinary receiving holding warehouse, not an automatic write-off warehouse.")
		)
	if existing := frappe.db.get_value(
		"Stock Entry",
		{
			"vuna_return_request": request.name,
			"outgoing_stock_entry": request.source_stock_entry,
			"docstatus": 0,
		},
		"name",
	):
		return existing
	stock = frappe.get_doc("Stock Entry", request.source_stock_entry)
	require_document_permission(stock, "read")
	if stock.docstatus != 1 or not stock.add_to_transit:
		frappe.throw(_("The original dispatch must still be submitted and in transit."))
	entry = frappe.new_doc("Stock Entry")
	entry.update(
		{
			"company": request.company,
			"stock_entry_type": "Material Transfer",
			"purpose": "Material Transfer",
			"outgoing_stock_entry": stock.name,
			"vuna_return_request": request.name,
			"from_warehouse": stock.to_warehouse,
			"to_warehouse": request.return_warehouse,
		}
	)
	for result in request.items:
		if result.custody_warehouse != stock.to_warehouse:
			continue
		qty = flt(result.requested_return_qty) - flt(result.received_qty)
		if qty <= 0:
			continue
		row = entry.append(
			"items",
			{
				"item_code": result.item_code,
				"uom": result.uom,
				"stock_uom": result.stock_uom,
				"conversion_factor": result.conversion_factor,
				"qty": qty,
				"s_warehouse": stock.to_warehouse,
				"t_warehouse": request.return_warehouse,
				"against_stock_entry": stock.name,
				"ste_detail": result.stock_entry_detail,
				"vuna_return_request_item": result.name,
			},
		)
		if result.batch_no or result.serial_no:
			# Warehouse staff may trim serials/quantities for a partial arrival using
			# ERPNext's standard picker. Final validation enforces the approved set.
			serials = [serial for serial in (result.serial_no or "").splitlines() if serial]
			used = set()
			for received in frappe.get_all(
				"Stock Entry Detail",
				filters={"vuna_return_request_item": result.name, "docstatus": 1},
				fields=["serial_and_batch_bundle", "serial_no", "batch_no", "transfer_qty"],
			):
				for group in stock_identities(received).values():
					used.update(group["serials"])
			serials = [serial for serial in serials if serial not in used]
			bundle = frappe.get_doc(
				{
					"doctype": "Serial and Batch Bundle",
					"company": request.company,
					"item_code": result.item_code,
					"warehouse": stock.to_warehouse,
					"type_of_transaction": "Outward",
					"voucher_type": "Stock Entry",
					"entries": [
						{"serial_no": serial, "batch_no": result.batch_no, "qty": -1} for serial in serials
					]
					if serials
					else [{"batch_no": result.batch_no, "qty": -qty * flt(result.conversion_factor)}],
				}
			).insert()
			row.serial_and_batch_bundle = bundle.name
	if not entry.items:
		frappe.throw(_("No outstanding rejected goods remain in this vehicle's transit warehouse."))
	entry.insert()
	return entry.name


def validate_vehicle_receipt(entry, method=None):
	previous = entry.get_doc_before_save()
	if (
		previous
		and previous.get("vuna_return_request") != entry.get("vuna_return_request")
		and (previous.get("outgoing_stock_entry") or entry.get("outgoing_stock_entry"))
	):
		frappe.throw(_("The Return Request link cannot be changed on a saved End Transit Stock Entry."))
	if (
		not entry.get("vuna_return_request")
		or not entry.outgoing_stock_entry
		or entry.get("vuna_unplanned_collection")
	):
		return
	request = frappe.get_doc("Return Request", entry.vuna_return_request)
	frappe.db.get_value("Return Request", request.name, "name", for_update=True)
	stock = frappe.get_doc("Stock Entry", entry.outgoing_stock_entry)
	if (
		request.docstatus != 1
		or request.return_context != "Depot"
		or request.source_stock_entry != stock.name
		or stock.docstatus != 1
		or not stock.add_to_transit
		or entry.add_to_transit
		or entry.company != request.company
		or entry.purpose != "Material Transfer"
	):
		frappe.throw(_("End Transit must reference the submitted original depot-return dispatch."))
	requests = {row.name: row for row in request.items}
	seen = set()
	for row in entry.items:
		approved = requests.get(row.vuna_return_request_item)
		if not approved or approved.name in seen or approved.custody_warehouse != stock.to_warehouse:
			frappe.throw(_("Receive each approved vehicle-held return row at most once."))
		if (
			row.ste_detail != approved.stock_entry_detail
			or row.against_stock_entry != stock.name
			or row.item_code != approved.item_code
			or row.s_warehouse != approved.custody_warehouse
			or row.t_warehouse != request.return_warehouse
			or row.uom != approved.uom
			or row.stock_uom != approved.stock_uom
			or flt(row.conversion_factor) != flt(approved.conversion_factor)
			or flt(row.qty) <= 0
		):
			frappe.throw(
				_("End Transit must preserve the approved source, receiving warehouse, item and UOM.")
			)
		other = frappe.db.sql(
			"""select coalesce(sum(d.qty),0) from `tabStock Entry Detail` d join `tabStock Entry` s on s.name=d.parent where s.docstatus=1 and d.vuna_return_request_item=%s and s.name!=%s and s.add_to_transit=0""",
			(approved.name, entry.name or ""),
		)[0][0]
		if flt(flt(row.qty) + flt(other) - flt(approved.requested_return_qty), row.precision("qty")) > 0:
			frappe.throw(_("Receipt exceeds the remaining approved vehicle-held return quantity."))
		groups = stock_identities(row)
		if set(groups) != {approved.batch_no or ""} or not set(
			groups[approved.batch_no or ""]["serials"]
		).issubset(set((approved.serial_no or "").splitlines())):
			frappe.throw(_("Receipt batches and serial numbers must match the rejected vehicle-held cargo."))
		seen.add(approved.name)


def sync_vehicle_receipt(entry, method=None):
	if entry.get("vuna_return_request"):
		from vuna_transport.services.execution.return_service import recalculate_return_request

		recalculate_return_request(entry.vuna_return_request)
