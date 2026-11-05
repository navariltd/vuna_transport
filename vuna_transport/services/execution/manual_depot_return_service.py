"""Approved depot returns against stock received earlier, without a rejection POD."""

import math
from collections import defaultdict

import frappe
from erpnext.utilities.transaction_base import validate_uom_is_integer
from frappe import _
from frappe.utils import flt, nowdate

from vuna_transport.services.execution.transfer_proof_service import stock_identities
from vuna_transport.services.shared.security_service import require_document_permission


def source_options(stock, warehouse):
	if stock.docstatus != 1 or stock.add_to_transit:
		frappe.throw(_("Select a submitted incoming Stock Entry, not an Add-to-Transit voucher."))
	options = []
	for row in stock.items:
		if row.t_warehouse != warehouse or flt(row.transfer_qty) <= 0:
			continue
		factor = flt(row.conversion_factor)
		if not math.isfinite(factor) or factor <= 0:
			frappe.throw(_("The original receipt has an invalid UOM conversion factor."))
		for batch, group in stock_identities(row).items():
			options.append(
				frappe._dict(
					stock_entry_detail=row.name,
					item_code=row.item_code,
					item_name=row.item_name,
					uom=row.uom,
					stock_uom=row.stock_uom,
					conversion_factor=row.conversion_factor,
					batch_no=batch or None,
					serial_no="\n".join(group["serials"]),
					source_bundle=row.serial_and_batch_bundle,
					custody_warehouse=warehouse,
					delivered_qty=group["stock_qty"] / factor,
				)
			)
	if not options:
		frappe.throw(_("This Stock Entry has no received items in the selected depot warehouse."))
	return options


@frappe.whitelist()
def get_depot_return_items(source_stock_entry: str, source_warehouse: str) -> list[dict]:
	stock = frappe.get_doc("Stock Entry", source_stock_entry)
	require_document_permission(stock, "read")
	require_document_permission(frappe.get_doc("Warehouse", source_warehouse), "read")
	return source_options(stock, source_warehouse)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_depot_receipt_query(
	doctype: str, txt: str, searchfield: str, start: int, page_len: int, filters: dict | str | None
) -> list:
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	if not filters.get("warehouse") or not filters.get("company"):
		return []
	require_document_permission(frappe.get_doc("Warehouse", filters["warehouse"]), "read")
	# get_list retains Stock Entry permission/user-permission conditions.
	names = frappe.get_list(
		"Stock Entry",
		filters={"docstatus": 1, "add_to_transit": 0, "company": filters["company"]},
		pluck="name",
		limit_page_length=0,
	)
	if not names:
		return []
	return frappe.db.sql(
		"""select distinct parent from `tabStock Entry Detail`
		where parent in %(names)s and t_warehouse=%(warehouse)s and parent like %(txt)s
		order by parent desc limit %(start)s, %(length)s""",
		{
			"names": names,
			"warehouse": filters["warehouse"],
			"txt": f"%{txt}%",
			"start": int(start),
			"length": int(page_len),
		},
	)


def validate_manual_depot_return(request):
	from vuna_transport.services.execution.return_v2_service import _validate_warehouse

	if not request.source_stock_entry or not request.source_warehouse:
		frappe.throw(_("Select the depot Source Warehouse and its incoming Stock Entry."))
	frappe.db.get_value("Stock Entry", request.source_stock_entry, "name", for_update=True)
	stock = frappe.get_doc("Stock Entry", request.source_stock_entry)
	require_document_permission(stock, "read")
	if stock.company != request.company:
		frappe.throw(_("The incoming Stock Entry must belong to the Return Request company."))
	for warehouse in (request.source_warehouse, request.return_warehouse):
		if not warehouse:
			frappe.throw(_("Select the pickup warehouse and receiving Return Warehouse."))
		_validate_warehouse(warehouse, request.company)
		if frappe.db.get_value("Warehouse", warehouse, "warehouse_type") == "Transit":
			frappe.throw(_("Manual depot returns require non-transit source and receiving warehouses."))
	if request.source_warehouse == request.return_warehouse:
		frappe.throw(_("Pickup and receiving warehouses must be different."))
	if "nl_vms" in frappe.get_installed_apps() and frappe.db.get_value(
		"Warehouse", request.return_warehouse, "is_rejected_warehouse"
	):
		frappe.throw(_("Choose a holding warehouse that does not automatically write off returned stock."))
	if any(
		request.get(field)
		for field in ("customer", "delivery_note", "sales_invoice", "credit_note", "return_stock_document")
	):
		frappe.throw(_("Depot returns use Stock Entries, not customer sales or credit documents."))
	options = {
		(row.stock_entry_detail, row.batch_no or ""): row
		for row in source_options(stock, request.source_warehouse)
	}
	quantities, serials = defaultdict(float), set()
	if not request.items:
		frappe.throw(_("Select at least one item to return."))
	precision = frappe.get_precision("Return Request Item", "requested_return_qty")
	for row in request.items:
		key = (row.stock_entry_detail, row.batch_no or "")
		source = options.get(key)
		qty = flt(row.requested_return_qty)
		if not source or row.item_code != source.item_code or not math.isfinite(qty) or qty <= 0:
			frappe.throw(_("Select a valid received item/batch and a positive return quantity."))
		chosen = [value.strip() for value in (row.serial_no or "").splitlines() if value.strip()]
		allowed = set(source.serial_no.splitlines())
		if (
			len(chosen) != len(set(chosen))
			or serials.intersection(chosen)
			or not set(chosen).issubset(allowed)
			or (allowed and len(chosen) != flt(qty * source.conversion_factor))
		):
			frappe.throw(_("Select the original serial numbers matching the requested quantity."))
		serials.update(chosen)
		for field in (
			"item_name",
			"uom",
			"stock_uom",
			"conversion_factor",
			"source_bundle",
			"custody_warehouse",
			"delivered_qty",
		):
			row.set(field, source[field])
		quantities[key] += qty
	# Locking the original voucher serializes approvals/edits against the same receipt.
	for existing in frappe.db.sql(
		"""select i.stock_entry_detail, i.batch_no, i.requested_return_qty, i.serial_no
		from `tabReturn Request Item` i join `tabReturn Request` r on r.name=i.parent
		where r.docstatus!=2 and r.source_stock_entry=%s and r.source_warehouse=%s
		and r.name!=%s for update""",
		(stock.name, request.source_warehouse, request.name or ""),
		as_dict=True,
	):
		key = (existing.stock_entry_detail, existing.batch_no or "")
		if key in quantities:
			quantities[key] += flt(existing.requested_return_qty)
		if serials.intersection((existing.serial_no or "").splitlines()):
			frappe.throw(_("A selected serial number is already reserved by another Return Request."))
	for key, quantity in quantities.items():
		if flt(quantity - options[key].delivered_qty, precision) > 0:
			frappe.throw(_("Return quantities exceed the original receipt after other active requests."))
	validate_uom_is_integer(request, "uom", "requested_return_qty", "Return Request Item")
	request.return_context, request.return_source = "Depot", "Manual"
	request.initiation_date = request.initiation_date or nowdate()
	request.rejecting_warehouse = request.source_warehouse
	from vuna_transport.services.execution.depot_return_service import depot_return_state
	from vuna_transport.services.execution.return_service import _apply_return_state

	_apply_return_state(request, depot_return_state(request))
