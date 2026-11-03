"""Remove only known legacy naming overrides preserved by DocType rename."""

import frappe


def execute():
	legacy = {
		"default": "DL-.DD.-.MM.-.YYYY.-",
		"options": "DL-.DD.-.MM.-.YYYY.-\nTL-.DD.-.MM.-.YYYY.-",
	}
	for property_name, value in legacy.items():
		frappe.db.delete(
			"Property Setter",
			{
				"doc_type": "Transport Load",
				"field_name": "naming_series",
				"property": property_name,
				"value": value,
			},
		)
	frappe.clear_cache(doctype="Transport Load")
