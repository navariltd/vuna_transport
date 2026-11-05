"""Adapt ERPNext Quality Inspection to Vuna-managed Sales Returns."""

import frappe
from erpnext.controllers.stock_controller import (
	check_item_quality_inspection as erpnext_check_item_quality_inspection,
)
from erpnext.controllers.stock_controller import make_quality_inspections as erpnext_make_quality_inspections
from frappe.utils import flt


class ReturnQualityInspectionMixin:
	def validate_inspection_required(self):
		if self._is_vuna_return_inspection():
			return
		return super().validate_inspection_required()

	def _is_vuna_return_inspection(self):
		if self.reference_type == "Stock Entry" and self.reference_name:
			return bool(
				frappe.db.exists(
					"Return Inspection",
					{
						"receiving_stock_entry": self.reference_name,
						"docstatus": ["!=", 2],
					},
				)
			)
		if self.reference_type != "Delivery Note" or not self.reference_name:
			return False
		if not frappe.db.get_value("Delivery Note", self.reference_name, "is_return"):
			return False
		return bool(
			frappe.db.get_value(
				"Return Request",
				{
					"return_stock_document": self.reference_name,
					"inspection_required": 1,
					"docstatus": 1,
				},
				"name",
			)
		)


@frappe.whitelist()
def check_item_quality_inspection(doctype, docstatus, items):
	"""Feed positive return quantities into ERPNext's standard inspection dialog."""
	parsed_items = frappe.parse_json(items) if isinstance(items, str) else items
	return_request = _get_vuna_return_request(doctype, items=parsed_items)
	if not return_request:
		return erpnext_check_item_quality_inspection(doctype, docstatus, parsed_items)

	request_items = set(
		frappe.get_all(
			"Return Request Item",
			filters={"parent": return_request},
			pluck="delivery_note_item",
		)
	)
	return [
		_normalize_return_inspection_item(item)
		for item in parsed_items
		if item.get("dn_detail") in request_items
	]


@frappe.whitelist()
def make_quality_inspections(company, doctype, docname, items, inspection_type):
	"""Use ERPNext's creator after normalizing Vuna Sales Return quantities."""
	parsed_items = frappe.parse_json(items) if isinstance(items, str) else items
	return_request = _get_vuna_return_request(doctype, docname=docname)
	if not return_request:
		return erpnext_make_quality_inspections(company, doctype, docname, parsed_items, inspection_type)

	inspections = erpnext_make_quality_inspections(
		company,
		doctype,
		docname,
		[_normalize_return_inspection_item(item) for item in parsed_items],
		"Incoming",
	)
	_link_inspections_to_return_request(return_request, docname, inspections)
	return inspections


def _get_vuna_return_request(doctype, docname=None, items=None):
	if doctype != "Delivery Note":
		return None
	if not docname:
		parents = {item.get("parent") for item in items or [] if item.get("parent")}
		if len(parents) != 1:
			return None
		docname = parents.pop()
	if not frappe.db.get_value("Delivery Note", docname, "is_return"):
		return None
	return frappe.db.get_value(
		"Return Request",
		{
			"return_stock_document": docname,
			"inspection_required": 1,
			"docstatus": 1,
		},
		"name",
	)


def _normalize_return_inspection_item(item):
	normalized = dict(item)
	normalized["qty"] = abs(flt(normalized.get("qty")))
	normalized["stock_qty"] = abs(flt(normalized.get("stock_qty")))
	if normalized.get("sample_quantity") is not None:
		normalized["sample_quantity"] = abs(flt(normalized.get("sample_quantity")))
	return normalized


def _link_inspections_to_return_request(return_request, delivery_note, inspections):
	return_rows = {
		row.name: row.dn_detail
		for row in frappe.get_all(
			"Delivery Note Item",
			filters={"parent": delivery_note},
			fields=["name", "dn_detail"],
		)
	}
	for inspection_name in inspections:
		child_row_reference = frappe.db.get_value(
			"Quality Inspection", inspection_name, "child_row_reference"
		)
		delivery_note_item = return_rows.get(child_row_reference)
		if not delivery_note_item:
			continue
		request_item = frappe.db.get_value(
			"Return Request Item",
			{"parent": return_request, "delivery_note_item": delivery_note_item},
			"name",
		)
		if request_item:
			frappe.db.set_value(
				"Return Request Item",
				request_item,
				"quality_inspection",
				inspection_name,
				update_modified=False,
			)
