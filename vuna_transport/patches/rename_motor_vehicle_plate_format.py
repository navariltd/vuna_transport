"""Use the clearer Motor Vehicle label for the standard Kenyan plate format."""

import frappe


def execute():
	frappe.db.set_value(
		"Vehicle Category",
		{"license_plate_format": "Standard Vehicle"},
		"license_plate_format",
		"Motor Vehicle",
		update_modified=False,
	)
