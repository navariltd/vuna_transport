"""Ensure Vuna custom fields are attributed to the Vuna Transport module."""

import frappe


CUSTOM_FIELD_DOCTYPES = {
	"Address",
	"Delivery Note",
	"Delivery Note Item",
	"Delivery Stop",
	"Delivery Trip",
	"Item",
	"Pick List",
	"Sales Order",
	"Vehicle",
}


def execute():
	"""Set module metadata only on custom fields owned by Vuna Transport."""
	for field in frappe.get_all(
		"Custom Field",
		filters={
			"dt": ["in", list(CUSTOM_FIELD_DOCTYPES)],
			"fieldname": ["like", "vuna_%"],
		},
		fields=["name", "module"],
		limit_page_length=0,
	):
		if field.module != "Vuna Transport":
			frappe.db.set_value(
				"Custom Field",
				field.name,
				"module",
				"Vuna Transport",
				update_modified=False,
			)
