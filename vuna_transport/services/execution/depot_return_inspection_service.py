"""Inspect posted depot return receipts; ERPNext remains the stock/QI authority."""

import math
from collections import defaultdict

import frappe
from erpnext.utilities.transaction_base import validate_uom_is_integer
from frappe import _
from frappe.utils import flt

from vuna_transport.services.execution.transfer_proof_service import stock_identities
from vuna_transport.services.shared.security_service import (
	require_doctype_permission,
	require_document_permission,
)


def receipt_sources(stock, request_name=None, required=True):
	if stock.docstatus != 1 or stock.add_to_transit or stock.purpose != "Material Transfer":
		frappe.throw(_("Inspect a submitted return receiving Material Transfer, not vehicle loading."))
	result = []
	for row in stock.items:
		request_item = row.get("vuna_return_request_item")
		if not request_item and row.ste_detail:
			request_item = frappe.db.get_value(
				"Stock Entry Detail", row.ste_detail, "vuna_return_request_item"
			)
		if not request_item:
			continue
		parent = frappe.db.get_value("Return Request Item", request_item, "parent")
		if request_name and parent != request_name:
			continue
		request = frappe.get_doc("Return Request", parent)
		if request.docstatus != 1 or request.return_context != "Depot" or request.company != stock.company:
			continue
		if row.t_warehouse != request.return_warehouse:
			continue
		if stock.get("vuna_return_request") != request.name:
			# Ordinary End Transit rows inherit lineage through their actual
			# outgoing pickup voucher, not an arbitrary editable child-row tag.
			if not stock.outgoing_stock_entry or not row.ste_detail:
				continue
			picked = frappe.db.get_value(
				"Stock Entry Detail", row.ste_detail, ["parent", "vuna_return_request_item"], as_dict=True
			)
			if (
				not picked
				or picked.parent != stock.outgoing_stock_entry
				or picked.vuna_return_request_item != request_item
			):
				continue
			if not frappe.db.get_value(
				"Stock Entry",
				{"name": picked.parent, "docstatus": 1, "add_to_transit": 1},
				"vuna_return_pickup_proof",
			):
				continue
		for batch, group in stock_identities(row).items():
			result.append(
				frappe._dict(
					name=row.name,
					source_row_idx=row.idx,
					stock_entry_detail=row.name,
					return_request_item=request_item,
					return_request=request.name,
					item_code=row.item_code,
					item_name=row.item_name or row.item_code,
					stock_uom=row.stock_uom,
					stock_qty=group["stock_qty"],
					batch_no=batch or None,
					serial_no="\n".join(group["serials"]),
					serial_and_batch_bundle=row.serial_and_batch_bundle,
					warehouse=row.t_warehouse,
				)
			)
	if not result and required:
		frappe.throw(
			_("This Stock Entry has no received depot-return stock in its approved Return Warehouse.")
		)
	return result


@frappe.whitelist()
def get_receipt_requests(receiving_stock_entry: str) -> list[str]:
	stock = frappe.get_doc("Stock Entry", receiving_stock_entry)
	require_document_permission(stock, "read")
	if stock.docstatus != 1 or stock.add_to_transit or stock.purpose != "Material Transfer":
		return []
	return sorted(
		{
			row.return_request
			for row in receipt_sources(stock, required=False)
			if frappe.has_permission("Return Request", "read", row.return_request)
		}
	)


@frappe.whitelist()
def create_inspection(receiving_stock_entry: str, return_request: str | None = None) -> str:
	stock = frappe.get_doc("Stock Entry", receiving_stock_entry)
	require_document_permission(stock, "read")
	require_doctype_permission("Return Inspection", "create")
	frappe.db.get_value("Stock Entry", stock.name, "name", for_update=True)
	sources = receipt_sources(stock, return_request)
	parents = {row.return_request for row in sources}
	if len(parents) != 1:
		frappe.throw(_("Specify one Return Request to inspect from this receipt."))
	request_name = parents.pop()
	require_document_permission(frappe.get_doc("Return Request", request_name), "read")
	if existing := frappe.db.get_value(
		"Return Inspection",
		{"receiving_stock_entry": stock.name, "return_request": request_name, "docstatus": ["!=", 2]},
		"name",
	):
		return existing
	inspection = frappe.new_doc("Return Inspection")
	inspection.inspection_required = frappe.db.get_single_value(
		"Transport Settings", "require_quality_inspection_for_return_inspection"
	)
	inspection.update(
		{
			"receiving_stock_entry": stock.name,
			"return_request": request_name,
			"company": stock.company,
			"source_warehouse": sources[0].warehouse,
		}
	)
	for source in sources:
		inspection.append(
			"results",
			{
				"stock_entry_detail": source.name,
				"return_request_item": source.return_request_item,
				"item_code": source.item_code,
				"stock_uom": source.stock_uom,
				"received_stock_qty": source.stock_qty,
				"inspection_qty": source.stock_qty,
				"batch_no": source.batch_no,
				"serial_no": source.serial_no,
				"serial_and_batch_bundle": source.serial_and_batch_bundle,
				"result": "Accepted",
			},
		)
	inspection.insert()
	return inspection.name


def validate_inspection(inspection):
	from vuna_transport.services.execution.return_v2_service import _validate_warehouse

	if inspection.sales_return:
		frappe.throw(_("Choose either a Sales Return or a receiving Stock Entry, not both."))
	stock = frappe.get_doc("Stock Entry", inspection.receiving_stock_entry)
	require_document_permission(stock, "read")
	frappe.db.get_value("Stock Entry", stock.name, "name", for_update=True)
	sources = receipt_sources(stock, inspection.return_request)
	if len({row.return_request for row in sources}) != 1:
		frappe.throw(_("Inspect one Return Request at a time."))
	inspection.return_request, inspection.company, inspection.customer = (
		sources[0].return_request,
		stock.company,
		None,
	)
	inspection.source_warehouse = sources[0].warehouse
	if frappe.db.exists(
		"Return Inspection",
		{
			"receiving_stock_entry": stock.name,
			"return_request": inspection.return_request,
			"docstatus": ["!=", 2],
			"name": ["!=", inspection.name or ""],
		},
	):
		frappe.throw(_("This receipt already has an active inspection for this Return Request."))
	options = {(row.name, row.batch_no or ""): row for row in sources}
	totals, used_serials, used_qi = defaultdict(float), set(), set()
	accepted = rejected = 0
	precision = frappe.get_precision("Return Inspection Result", "inspection_qty")
	from vuna_transport.services.execution.return_inspection_quality_service import (
		validate_result_quality_inspection,
	)

	for row in inspection.results:
		key = (row.stock_entry_detail, row.batch_no or "")
		source = options.get(key)
		if not source and not row.stock_entry_detail:
			candidates = [
				item
				for item in sources
				if item.item_code == row.item_code and (item.batch_no or "") == (row.batch_no or "")
			]
			if len(candidates) == 1:
				source = candidates[0]
				key = (source.name, source.batch_no or "")
		if not source or row.item_code != source.item_code or row.delivery_note_item:
			frappe.throw(_("Row #{0}: Select the received item/batch from this Stock Entry.").format(row.idx))
		qty = flt(row.inspection_qty)
		if not math.isfinite(qty) or qty <= 0 or row.result not in ("Accepted", "Rejected"):
			frappe.throw(_("Inspection results require positive finite quantities and Accepted or Rejected."))
		if row.result == "Rejected" and not row.reason:
			frappe.throw(_("Row #{0}: Rejection Reason is required.").format(row.idx))
		if inspection.docstatus == 1 and not row.target_warehouse:
			frappe.throw(_("Row #{0}: Select a Target Warehouse.").format(row.idx))
		if row.target_warehouse:
			_validate_warehouse(row.target_warehouse, stock.company)
			if (
				row.target_warehouse == source.warehouse
				or frappe.db.get_value("Warehouse", row.target_warehouse, "warehouse_type") == "Transit"
			):
				frappe.throw(_("Select a distinct non-transit target warehouse for inspected goods."))
			if "nl_vms" in frappe.get_installed_apps() and frappe.db.get_value(
				"Warehouse", row.target_warehouse, "is_rejected_warehouse"
			):
				frappe.throw(
					_(
						"Use an ordinary holding warehouse; this rejected warehouse automatically writes off stock."
					)
				)
		chosen = [value.strip() for value in (row.serial_no or "").splitlines() if value.strip()]
		allowed = set(source.serial_no.splitlines())
		if (
			len(chosen) != len(set(chosen))
			or used_serials.intersection(chosen)
			or not set(chosen).issubset(allowed)
			or (allowed and len(chosen) != qty)
		):
			frappe.throw(_("Choose unique received serial numbers matching each inspection group quantity."))
		used_serials.update(chosen)
		row.stock_entry_detail, row.return_request_item = source.name, source.return_request_item
		row.stock_uom, row.received_stock_qty = source.stock_uom, source.stock_qty
		row.serial_and_batch_bundle = source.serial_and_batch_bundle
		validate_result_quality_inspection(inspection, row, used_qi)
		totals[key] += qty
		if row.result == "Accepted":
			accepted += qty
		else:
			rejected += qty
	for key, source in options.items():
		balance = flt(totals[key] - source.stock_qty, precision)
		if balance > 0 or (inspection.docstatus == 1 and balance != 0):
			frappe.throw(
				_(
					"Inspection groups for {0}, batch {1}, must total received quantity {2} {3} before submission."
				).format(source.item_code, source.batch_no or "—", source.stock_qty, source.stock_uom)
			)
	inspection.total_received_stock_qty = sum(row.stock_qty for row in sources)
	inspection.total_inspected_stock_qty = accepted + rejected
	inspection.accepted_stock_qty, inspection.rejected_stock_qty = accepted, rejected
	inspection.pending_stock_qty = max(inspection.total_received_stock_qty - accepted - rejected, 0)
	validate_uom_is_integer(inspection, "stock_uom", "inspection_qty", "Return Inspection Result")


@frappe.whitelist()
def get_item_options(
	receiving_stock_entry: str, return_request: str, item_code: str | None = None, batch_no: str | None = None
) -> list[dict]:
	stock = frappe.get_doc("Stock Entry", receiving_stock_entry)
	require_document_permission(stock, "read")
	return [
		row
		for row in receipt_sources(stock, return_request)
		if (not item_code or row.item_code == item_code) and (not batch_no or row.batch_no == batch_no)
	]


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def item_query(
	doctype: str, txt: str, searchfield: str, start: int, page_len: int, filters: dict | str | None
) -> list:
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	if not filters.get("receiving_stock_entry") or not filters.get("return_request"):
		return []
	rows = get_item_options(
		filters["receiving_stock_entry"], filters["return_request"], filters.get("item_code")
	)
	values = sorted(
		{
			(row.batch_no, row.batch_no) if doctype == "Batch" else (row.item_code, row.item_name)
			for row in rows
			if doctype != "Batch" or row.batch_no
		}
	)
	return [row for row in values if txt.lower() in " ".join(row).lower()][
		int(start) : int(start) + int(page_len)
	]


def create_stock_entry(inspection):
	"""Keep batch/serial outcome groups exact; never auto-select other returned lots."""
	validate_inspection(inspection)
	entry = frappe.new_doc("Stock Entry")
	entry.update(
		{
			"company": inspection.company,
			"purpose": "Material Transfer",
			"stock_entry_type": "Material Transfer",
		}
	)
	for result in inspection.results:
		row = entry.append(
			"items",
			{
				"item_code": result.item_code,
				"s_warehouse": inspection.source_warehouse,
				"t_warehouse": result.target_warehouse,
				"qty": result.inspection_qty,
				"transfer_qty": result.inspection_qty,
				"uom": result.stock_uom,
				"stock_uom": result.stock_uom,
				"conversion_factor": 1,
			},
		)
		if result.batch_no or result.serial_no:
			serials = [serial for serial in (result.serial_no or "").splitlines() if serial]
			bundle = frappe.get_doc(
				{
					"doctype": "Serial and Batch Bundle",
					"item_code": result.item_code,
					"company": entry.company,
					"warehouse": inspection.source_warehouse,
					"type_of_transaction": "Outward",
					"voucher_type": "Stock Entry",
					"entries": [
						{"serial_no": serial, "batch_no": result.batch_no, "qty": -1} for serial in serials
					]
					if serials
					else [{"batch_no": result.batch_no, "qty": -flt(result.inspection_qty)}],
				}
			).insert()
			row.serial_and_batch_bundle = bundle.name
	entry.insert()
	inspection.db_set("stock_entry", entry.name, update_modified=True)
	return entry.name


def validate_stock_entry(entry, inspection):
	expected, actual = defaultdict(float), defaultdict(float)
	expected_serials, actual_serials = set(), set()
	for row in inspection.results:
		expected[(row.item_code, row.target_warehouse, row.batch_no or "")] += flt(row.inspection_qty)
		for serial in (serial for serial in (row.serial_no or "").splitlines() if serial):
			expected_serials.add((row.item_code, row.target_warehouse, serial))
	if entry.company != inspection.company or entry.purpose != "Material Transfer" or entry.add_to_transit:
		frappe.throw(_("Inspection stock must remain a same-company Material Transfer."))
	for row in entry.items:
		if row.s_warehouse != inspection.source_warehouse or flt(row.qty) <= 0:
			frappe.throw(
				_("Inspection stock must leave its received returns warehouse with positive quantities.")
			)
		for batch, group in stock_identities(row).items():
			actual[(row.item_code, row.t_warehouse, batch)] += group["stock_qty"]
			for serial in group["serials"]:
				identity = (row.item_code, row.t_warehouse, serial)
				if identity in actual_serials:
					frappe.throw(_("A serial number appears more than once."))
				actual_serials.add(identity)
	precision = frappe.get_precision("Stock Entry Detail", "transfer_qty")
	if (
		set(actual) != set(expected)
		or any(flt(actual[key] - qty, precision) for key, qty in expected.items())
		or actual_serials != expected_serials
	):
		frappe.throw(
			_(
				"Stock Entry quantities, batches, serials and target warehouses must match the Return Inspection."
			)
		)
