"""Remove rename-generated overrides so delivery DocTypes use their source naming series."""

import frappe


def execute():
	frappe.db.delete(
		"Property Setter",
		{
			"doc_type": ("in", ("Delivery Order", "Transport Load")),
			"field_name": "naming_series",
			"property": ("in", ("default", "options")),
		},
	)
	frappe.clear_cache(doctype="Delivery Order")
	frappe.clear_cache(doctype="Transport Load")
