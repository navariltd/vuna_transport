"""Installation helpers for Vuna Transport."""

import frappe


DEFAULT_TRANSPORT_UOMS = {
	"default_weight_uom": "Kg",
	"default_volume_uom": "Cubic Meter",
	"default_distance_uom": "Kilometer",
}


def after_install():
	ensure_transport_uoms()


def ensure_transport_uoms():
	"""Ensure transport defaults exist after ERPNext masters are initialized."""
	for uom_name in DEFAULT_TRANSPORT_UOMS.values():
		if not frappe.db.exists("UOM", uom_name):
			frappe.get_doc(
				{
					"doctype": "UOM",
					"uom_name": uom_name,
					"enabled": 1,
				}
			).insert(ignore_permissions=True)

	for fieldname, uom_name in DEFAULT_TRANSPORT_UOMS.items():
		if not frappe.db.get_single_value("Transport Settings", fieldname):
			frappe.db.set_single_value("Transport Settings", fieldname, uom_name)

	frappe.db.commit()


def migrate_province_to_territory():
	"""Move development-era Province links to ERPNext non-group Territories."""
	if frappe.db.has_column("Address", "vuna_province") and frappe.db.has_column("Address", "vuna_territory"):
		for row in frappe.db.sql(
			"""
			select name, vuna_province
			from `tabAddress`
			where ifnull(vuna_province, '') != '' and ifnull(vuna_territory, '') = ''
			""",
			as_dict=True,
		):
			territory = frappe.db.get_value(
				"Territory", {"name": row.vuna_province, "is_group": 0}, "name"
			)
			if territory:
				frappe.db.set_value("Address", row.name, "vuna_territory", territory, update_modified=False)

	if frappe.db.has_column("County", "province") and frappe.db.has_column("County", "territory"):
		for row in frappe.db.sql(
			"""
			select name, province
			from `tabCounty`
			where ifnull(province, '') != '' and ifnull(territory, '') = ''
			""",
			as_dict=True,
		):
			territory = frappe.db.get_value(
				"Territory", {"name": row.province, "is_group": 0}, "name"
			)
			if territory:
				frappe.db.set_value("County", row.name, "territory", territory, update_modified=False)

	if frappe.db.has_column("Transport Destination Rate", "province") and frappe.db.has_column(
		"Transport Destination Rate", "territory"
	):
		frappe.db.sql(
			"""
			update `tabTransport Destination Rate` r
			join `tabTerritory` t on t.name = r.province and t.is_group = 0
			set r.territory = t.name
			where ifnull(r.province, '') != '' and ifnull(r.territory, '') = ''
			"""
		)
