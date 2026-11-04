"""One-off development consolidation, preserving proof identities and evidence.

Run after installing unified POD fields and before removing the experimental
Transport Execution Proof DocTypes. No stock or accounting documents are posted.
"""

import frappe
from frappe.utils import flt, now_datetime


def execute():
	old_type = "Transport Execution Proof"
	if not frappe.db.exists("DocType", old_type):
		return {"converted": [], "already_removed": True}
	converted = []
	for name in frappe.get_all(old_type, pluck="name"):
		old = frappe.get_doc(old_type, name)
		if frappe.db.exists("Proof of Delivery", name):
			pod = frappe.get_doc("Proof of Delivery", name)
			if pod.transport_trip_task != old.transport_trip_task:
				frappe.throw(
					f"POD identity collision: {name}. Consolidation stopped without overwriting evidence."
				)
			continue
		task = frappe.get_doc("Transport Trip Task", old.transport_trip_task)
		pod = frappe.new_doc("Proof of Delivery")
		pod.name = old.name
		pod.naming_series = "POD-.DD.-.MM.-.YYYY.-"
		for field in (
			"company",
			"delivery_trip",
			"delivery_stop",
			"transport_trip_task",
			"proof_type",
			"dispatch_stock_entry",
			"outcome",
			"remarks",
			"recorded_by",
			"mobile_request_id",
			"payload_hash",
		):
			pod.set(field, old.get(field))
		pod.delivery_load, pod.delivery_address = task.transport_load, task.address
		pod.received_by = old.confirmed_by
		pod.delivery_datetime = old.event_datetime or old.creation or now_datetime()
		pod.direction = "Dispatch" if old.proof_type == "Transfer Dispatch" else "Delivery"
		pod.status = (
			"Accepted"
			if old.outcome == "Completed"
			else "Failed"
			if old.outcome == "Failed"
			else "Partially Delivered"
		)
		pod.docstatus, pod.owner, pod.creation, pod.modified, pod.modified_by = (
			old.docstatus,
			old.owner,
			old.creation,
			old.modified,
			old.modified_by,
		)
		for item in old.items:
			row = pod.append(
				"items",
				{
					field: item.get(field)
					for field in (
						"item_code",
						"source_row",
						"batch_no",
						"serial_no",
						"uom",
						"stock_uom",
						"conversion_factor",
						"stock_qty",
						"source_bundle",
					)
				},
			)
			row.name = item.name
			row.ordered_qty, row.delivered_qty, row.rejected_qty = item.planned_qty, item.qty, 0
			# The old proof never captured explicit rejection. A shortage cannot be
			# retroactively classified as returned stock without evidence.
			row.missing_qty = max(flt(item.planned_qty) - flt(item.qty), 0)
			row.docstatus = old.docstatus
		pod.total_accepted_qty = sum(flt(row.delivered_qty) for row in pod.items)
		pod.total_rejected_qty = 0
		pod.db_insert()
		for row in pod.items:
			row.db_insert()
		for doctype, type_field, name_field in (
			("File", "attached_to_doctype", "attached_to_name"),
			("Comment", "reference_doctype", "reference_name"),
			("Communication", "reference_doctype", "reference_name"),
			("Version", "ref_doctype", "docname"),
			("DocShare", "share_doctype", "share_name"),
		):
			frappe.db.set_value(
				doctype,
				{type_field: old_type, name_field: name},
				type_field,
				"Proof of Delivery",
				update_modified=False,
			)
		converted.append(name)
	return {"converted": converted, "stock_posted": False, "identifiers_preserved": True}


def verify():
	rows = frappe.get_all("Transport Execution Proof", fields=["name", "transport_trip_task", "docstatus"])
	for row in rows:
		pod = frappe.db.get_value(
			"Proof of Delivery", row.name, ["transport_trip_task", "docstatus"], as_dict=True
		)
		if not pod or pod.transport_trip_task != row.transport_trip_task or pod.docstatus != row.docstatus:
			frappe.throw(f"Proof conversion not verified: {row.name}")
		old_items = frappe.get_all(
			"Transport Execution Proof Item",
			filters={"parent": row.name},
			fields=["name", "qty", "planned_qty"],
		)
		for item in old_items:
			new = frappe.db.get_value(
				"Proof of Delivery Item", item.name, ["parent", "delivered_qty", "ordered_qty"], as_dict=True
			)
			if (
				not new
				or new.parent != row.name
				or flt(new.delivered_qty) != flt(item.qty)
				or flt(new.ordered_qty) != flt(item.planned_qty)
			):
				frappe.throw(f"Proof item conversion not verified: {item.name}")
	return {"verified": len(rows), "identifiers_and_quantities_preserved": True}
