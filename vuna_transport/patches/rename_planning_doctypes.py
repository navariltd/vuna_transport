"""Rename the MVP planning DocTypes to the revised delivery terminology."""

import frappe

DOCTYPE_RENAMES = (
	("Transport Requirement Item", "Delivery Order Item"),
	("Transport Load Requirement", "Delivery Load Order"),
	("Transport Requirement", "Delivery Order"),
	("Transport Load", "Delivery Load"),
	("Vehicle Type", "Vehicle Category"),
)


def execute():
	for old_name, new_name in DOCTYPE_RENAMES:
		if frappe.db.exists("DocType", new_name):
			continue
		if not frappe.db.exists("DocType", old_name):
			continue

		frappe.rename_doc(
			"DocType",
			old_name,
			new_name,
			force=True,
			show_alert=False,
			rebuild_search=False,
		)
