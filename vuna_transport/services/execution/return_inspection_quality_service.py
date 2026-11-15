"""Row-specific quality evidence for customer and depot Return Inspections."""

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime

from vuna_transport.services.shared.security_service import (
	require_doctype_permission,
	require_document_permission,
)


def quality_inspection_required(inspection):
	value = inspection.get("inspection_required")
	if value is None:
		value = frappe.db.get_single_value(
			"Transport Settings", "require_quality_inspection_for_return_inspection"
		)
	return bool(cint(value))


def validate_result_quality_inspection(inspection, row, used):
	"""Draft evidence can remain incomplete; submission requires the matching outcome."""
	if not row.quality_inspection:
		if inspection.docstatus == 1 and quality_inspection_required(inspection):
			frappe.throw(
				_("Row #{0}: A submitted Quality Inspection is required for this result group.").format(
					row.idx
				)
			)
		return
	if row.quality_inspection in used:
		frappe.throw(_("Each result group requires its own Quality Inspection."))
	if frappe.db.exists(
		"Return Inspection Result",
		{"quality_inspection": row.quality_inspection, "docstatus": ["!=", 2], "name": ["!=", row.name]},
	):
		frappe.throw(
			_("Quality Inspection {0} is already linked to another result group.").format(
				row.quality_inspection
			)
		)
	used.add(row.quality_inspection)
	qi = frappe.get_doc("Quality Inspection", row.quality_inspection)
	require_document_permission(qi, "read")
	depot = bool(inspection.get("receiving_stock_entry"))
	if (
		qi.docstatus == 2
		or qi.reference_type != ("Stock Entry" if depot else "Delivery Note")
		or qi.reference_name != (inspection.receiving_stock_entry if depot else inspection.sales_return)
		or qi.child_row_reference != (row.stock_entry_detail if depot else row.delivery_note_item)
		or qi.item_code != row.item_code
		or (row.batch_no and qi.batch_no != row.batch_no)
	):
		frappe.throw(
			_("Row #{0}: Quality Inspection must match the received item, source row and batch.").format(
				row.idx
			)
		)
	if inspection.docstatus == 1:
		if qi.docstatus != 1:
			frappe.throw(_("Row #{0}: Quality Inspection {1} must be submitted.").format(row.idx, qi.name))
		if qi.status != row.result:
			frappe.throw(_("Row #{0}: Quality Inspection outcome must be {1}.").format(row.idx, row.result))
		if flt(qi.sample_size) > flt(row.inspection_qty):
			frappe.throw(
				_("Row #{0}: Quality Inspection sample size exceeds this result group's quantity.").format(
					row.idx
				)
			)


@frappe.whitelist()
def create_quality_inspections(return_inspection: str, result_rows: list | str, modified: str) -> dict:
	"""Explicitly create QIs from selected, saved result rows, without duplicates."""
	require_doctype_permission("Quality Inspection", "create")
	inspection = frappe.get_doc("Return Inspection", return_inspection, for_update=True)
	require_document_permission(inspection, "write")
	if inspection.docstatus != 0:
		frappe.throw(_("Quality Inspections can only be created from a draft Return Inspection."))
	if not modified or get_datetime(inspection.modified) != get_datetime(modified):
		frappe.throw(_("This Return Inspection has changed. Reload it before creating Quality Inspections."))
	names = frappe.parse_json(result_rows) if isinstance(result_rows, str) else result_rows
	if (
		not isinstance(names, list)
		or not 1 <= len(names) <= 500
		or any(not isinstance(name, str) for name in names)
	):
		frappe.throw(_("Select between 1 and 500 result rows."))
	available = {row.name for row in inspection.results}
	if len(set(names)) != len(names) or not set(names).issubset(available):
		frappe.throw(_("Select distinct result rows belonging to this Return Inspection."))
	created = create_missing_quality_inspections(inspection, set(names))
	if created:
		inspection.save()
	return {"created": created}


def create_missing_quality_inspections(inspection, result_rows=None):
	"""Called only by the explicit Create action, never by a document save."""
	if inspection.docstatus != 0:
		return []
	created = []
	depot = bool(inspection.get("receiving_stock_entry"))
	for row in inspection.results:
		if row.quality_inspection or (result_rows is not None and row.name not in result_rows):
			continue
		qi = frappe.new_doc("Quality Inspection")
		qi.update(
			{
				"company": inspection.company,
				"inspection_type": "Incoming",
				"reference_type": "Stock Entry" if depot else "Delivery Note",
				"reference_name": inspection.receiving_stock_entry if depot else inspection.sales_return,
				"child_row_reference": row.stock_entry_detail if depot else row.delivery_note_item,
				"item_code": row.item_code,
				"batch_no": row.batch_no,
				"item_serial_no": next(iter((row.serial_no or "").splitlines()), None),
				"sample_size": 0,
				"inspected_by": frappe.session.user,
			}
		)
		# ERPNext's source-row backlink cannot represent separate accepted/rejected groups.
		qi.flags.vuna_return_inspection = True
		qi.insert()
		row.quality_inspection = qi.name
		frappe.db.set_value(
			"Return Inspection Result", row.name, "quality_inspection", qi.name, update_modified=False
		)
		created.append(qi.name)
	return created
