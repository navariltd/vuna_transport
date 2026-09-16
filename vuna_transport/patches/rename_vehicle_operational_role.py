"""Rename vehicle operational roles to the clearer Vehicle Classification model."""

import frappe


def execute():
	for old_name, new_name in (
		("Vehicle Operational Role Item", "Vehicle Classification Item"),
		("Vehicle Operational Role", "Vehicle Classification"),
	):
		if frappe.db.exists("DocType", new_name) or not frappe.db.exists("DocType", old_name):
			continue
		frappe.rename_doc(
			"DocType",
			old_name,
			new_name,
			force=True,
			show_alert=False,
			rebuild_search=False,
		)

	if frappe.db.exists("Custom Field", "Vehicle-vuna_operational_roles"):
		frappe.db.set_value(
			"Custom Field",
			"Vehicle-vuna_operational_roles",
			"options",
			"Vehicle Classification Item",
			update_modified=False,
		)
