"""Return quantities use a chosen transaction UOM and a server-owned stock factor."""

import math

import frappe
from frappe import _
from frappe.utils import flt

from vuna_transport.services.shared.security_service import require_document_permission


def return_conversion_factor(source, uom):
	# Preserve the factor actually used on the original transaction, even if
	# somebody subsequently changes the Item's conversion table.
	if uom == source.stock_uom:
		factor = 1
	elif uom == source.uom:
		factor = flt(source.conversion_factor)
	else:
		item = frappe.get_cached_doc("Item", source.item_code)
		factors = {row.uom: row.conversion_factor for row in item.uoms}
		if item.variant_of:
			template = frappe.get_cached_doc("Item", item.variant_of)
			factors = {**{row.uom: row.conversion_factor for row in template.uoms}, **factors}
		factor = flt(factors.get(uom))
	if not math.isfinite(factor) or factor <= 0:
		frappe.throw(_("UOM {0} has no valid conversion for Item {1}.").format(uom, source.item_code))
	return factor


def _return_source(source_doctype, source_row):
	parents = {
		"Delivery Note Item": "Delivery Note",
		"Sales Invoice Item": "Sales Invoice",
		"Stock Entry Detail": "Stock Entry",
		"Proof of Delivery Item": "Proof of Delivery",
	}
	if source_doctype not in parents:
		frappe.throw(_("Unsupported return source."))
	source = frappe.get_doc(source_doctype, source_row)
	require_document_permission(frappe.get_doc(parents[source_doctype], source.parent), "read")
	return source


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_return_uom_query(
	doctype: str, txt: str, searchfield: str, start: int, page_len: int, filters: dict | str | None = None
) -> list:
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	if not filters.get("source_row"):
		return []
	source = _return_source(filters.get("source_doctype"), filters["source_row"])
	item = frappe.get_cached_doc("Item", source.item_code)
	uoms = {source.uom, source.stock_uom, *(row.uom for row in item.uoms)}
	if item.variant_of:
		uoms.update(row.uom for row in frappe.get_cached_doc("Item", item.variant_of).uoms)
	return frappe.get_list(
		"UOM",
		filters={"name": ["in", list(uoms)], "enabled": 1},
		or_filters={"name": ["like", f"%{txt}%"]},
		fields=["name"],
		start=start,
		limit=min(page_len, 100),
		as_list=True,
	)


def approved_return_uom(row):
	approved = None
	if (
		row.get("doctype") == "Return Request Item"
		and row.get("docstatus") == 1
		and frappe.db.get_value("Return Request", row.parent, "docstatus") == 1
	):
		approved = frappe.db.get_value(
			"Return Request Item", row.name, ["uom", "stock_uom", "conversion_factor"], as_dict=True
		)
	return approved


def configure_return_uom(row, source):
	row.uom = row.get("uom") or source.uom
	row.stock_uom = source.stock_uom
	approved = approved_return_uom(row)
	if approved:
		if (
			row.uom != approved.uom
			or row.stock_uom != approved.stock_uom
			or flt(row.conversion_factor) != flt(approved.conversion_factor)
		):
			frappe.throw(_("The UOM and conversion factor cannot change after Return Request submission."))
		row.conversion_factor = flt(approved.conversion_factor)
	else:
		row.conversion_factor = return_conversion_factor(source, row.uom)
	if source.get("delivered_qty") is not None:
		delivered_stock = abs(flt(source.delivered_qty)) * (flt(source.conversion_factor) or 1)
	else:
		delivered_stock = abs(
			flt(
				source.get("stock_qty")
				or source.get("transfer_qty")
				or flt(source.get("qty")) * (flt(source.conversion_factor) or 1)
			)
		)
	row.delivered_qty = delivered_stock / row.conversion_factor
	precision = row.precision("requested_return_qty") if callable(getattr(row, "precision", None)) else None
	quantity = flt(row.requested_return_qty, precision)
	row.requested_return_qty = quantity
	stock_quantity = quantity * row.conversion_factor
	if not math.isfinite(quantity) or not math.isfinite(stock_quantity) or quantity <= 0:
		frappe.throw(_("Return quantities must be finite and greater than zero."))
	for uom, amount in ((row.uom, quantity), (row.stock_uom, stock_quantity)):
		if (
			frappe.db.get_value("UOM", uom, "must_be_whole_number") == 1
			and abs(amount - round(amount)) > 1e-9
		):
			frappe.throw(_("UOM {0} requires whole-number return quantities.").format(uom))


@frappe.whitelist()
def get_return_uom_details(source_doctype: str, source_row: str, uom: str) -> dict:
	source = _return_source(source_doctype, source_row)
	return {"conversion_factor": return_conversion_factor(source, uom), "stock_uom": source.stock_uom}


def copy_return_commercial_terms(row, source, fields):
	"""Convert source per-unit money amounts; keep percentages and pricing lineage."""
	scale = (flt(getattr(row, "conversion_factor", None)) or 1) / (flt(source.get("conversion_factor")) or 1)
	unit_amounts = {
		"price_list_rate",
		"base_price_list_rate",
		"rate_with_margin",
		"base_rate_with_margin",
		"discount_amount",
		"rate",
		"base_rate",
	}
	for field in fields:
		value = source.get(field)
		if value is not None and (
			field in unit_amounts
			or (field == "margin_rate_or_amount" and source.get("margin_type") == "Amount")
		):
			value = flt(value) * scale
		setattr(row, field, value)


def apply_sales_return_uom(row, request_item, fields):
	# Do this before setting qty: mapped stock_qty still limits the original
	# returnable quantity, but qty/rate will now be expressed in the chosen UOM.
	source = frappe._dict({field: getattr(row, field, None) for field in (*fields, "conversion_factor")})
	row.uom = request_item.uom
	row.stock_uom = request_item.stock_uom
	row.conversion_factor = request_item.conversion_factor
	copy_return_commercial_terms(row, source, fields)
