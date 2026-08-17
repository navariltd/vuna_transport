"""Align Custom Field document names with their renamed fieldnames."""

import frappe

from vuna_transport.patches.rename_delivery_custom_fields import CUSTOM_FIELD_RENAMES


def execute():
	for doctype, old_fieldname, new_fieldname in CUSTOM_FIELD_RENAMES:
		old_name = f"{doctype}-{old_fieldname}"
		new_name = f"{doctype}-{new_fieldname}"
		if not frappe.db.exists("Custom Field", old_name) or frappe.db.exists("Custom Field", new_name):
			continue
		frappe.rename_doc(
			"Custom Field",
			old_name,
			new_name,
			force=True,
			show_alert=False,
			rebuild_search=False,
		)
