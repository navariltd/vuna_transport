"""Migrate legacy Province geography to ERPNext Territory and remove old fields."""

import frappe

from vuna_transport.install import migrate_province_to_territory


LEGACY_FIELDS = (
	("Address", "vuna_province", "Address-vuna_province"),
	("County", "province", None),
	("Transport Destination Rate", "province", None),
)


def execute():
	"""Run once after the replacement Territory fields have been synchronized."""
	migrate_province_to_territory()

	for doctype, fieldname, custom_field_name in LEGACY_FIELDS:
		if custom_field_name and frappe.db.exists("Custom Field", custom_field_name):
			frappe.delete_doc("Custom Field", custom_field_name, ignore_missing=True)
		if frappe.db.has_column(doctype, fieldname):
			safe_doctype = ("tab" + doctype).replace("`", "``")
			frappe.db.sql_ddl(
				f"alter table `{safe_doctype}` drop column `{fieldname}`"
			)
