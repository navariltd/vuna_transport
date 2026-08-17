"""Rename Vuna fields on standard ERPNext DocTypes before custom-field sync."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import rename_fieldname


CUSTOM_FIELD_RENAMES = (
	("Pick List", "vuna_transport_load", "vuna_delivery_load"),
	("Delivery Note", "vuna_transport_load", "vuna_delivery_load"),
	("Delivery Note", "vuna_transport_requirement", "vuna_delivery_order"),
	("Delivery Trip", "vuna_transport_load", "vuna_delivery_load"),
	("Delivery Stop", "vuna_transport_requirement", "vuna_delivery_order"),
	("Delivery Stop", "vuna_transport_load_requirement", "vuna_delivery_load_order"),
	("Vehicle", "vuna_vehicle_type", "vuna_vehicle_category"),
)


def execute():
	for doctype, old_fieldname, new_fieldname in CUSTOM_FIELD_RENAMES:
		if frappe.db.exists("Custom Field", {"dt": doctype, "fieldname": new_fieldname}):
			continue

		custom_field = frappe.db.get_value(
			"Custom Field",
			{"dt": doctype, "fieldname": old_fieldname},
			"name",
		)
		if custom_field:
			rename_fieldname(custom_field, new_fieldname)
