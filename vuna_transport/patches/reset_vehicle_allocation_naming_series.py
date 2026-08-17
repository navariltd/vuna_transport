"""Remove stale naming-series overrides from the earlier development prototype."""

import frappe


def execute():
	frappe.db.delete(
		"Property Setter",
		{
			"doc_type": "Vehicle Allocation",
			"field_name": "naming_series",
			"property": ("in", ("default", "options")),
		},
	)
	frappe.clear_cache(doctype="Vehicle Allocation")
