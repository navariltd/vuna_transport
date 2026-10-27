"""Normalize existing valid transport Vehicle registration marks."""

import frappe

from vuna_transport.services.fleet.license_plate_service import normalize_and_validate_license_plate


def execute():
	for doctype, category_field, enabled_field in (
		("Vehicle", "vuna_vehicle_category", "vuna_transport_enabled"),
		("Vehicle Onboarding", "vehicle_category", None),
	):
		fields = ["name", "license_plate", category_field]
		if enabled_field:
			fields.append(enabled_field)
		for row in frappe.get_all(doctype, fields=fields, limit_page_length=0):
			if enabled_field and not row.get(enabled_field):
				continue
			try:
				normalized = normalize_and_validate_license_plate(
					row.license_plate, row.get(category_field)
				)
			except frappe.ValidationError:
				# Do not prevent an upgrade because of legacy data. It will be corrected on its next edit.
				continue
			if normalized != row.license_plate:
				frappe.db.set_value(doctype, row.name, "license_plate", normalized, update_modified=False)
