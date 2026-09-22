"""Installation helpers for Vuna Transport."""

import frappe


DEFAULT_TRANSPORT_UOMS = {
	"default_weight_uom": "Kg",
	"default_volume_uom": "Cubic Meter",
	"default_distance_uom": "Kilometer",
}

DEFAULT_DISPATCH_SCAN_ROLES = {
	"allowed_loaded_scan_roles": ("Transport Manager",),
	"allowed_dispatched_scan_roles": ("Transport Dispatcher",),
}

LEGACY_DEFAULT_DISPATCH_SCAN_ROLES = {
	"allowed_loaded_scan_roles": {"Transport Dispatcher", "Transport Manager"},
	"allowed_dispatched_scan_roles": {"Transport Dispatcher", "Transport Manager"},
}


def after_install():
	ensure_transport_uoms()
	normalize_transport_cost_natures()
	ensure_dispatch_scan_roles()


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


def normalize_transport_cost_natures():
	"""Convert the retired Accessorial / Actual value to the supported Fixed nature."""
	for doctype, fieldname in (
		("Transport Cost Component", "default_cost_nature"),
		("Transport Cost Structure Component", "cost_nature"),
		("Transport Cost Structure Assignment Component", "cost_nature"),
	):
		if frappe.db.has_column(doctype, fieldname):
			frappe.db.sql(
				f"""
				update `tab{doctype}`
				set `{fieldname}` = 'Fixed'
				where `{fieldname}` = 'Accessorial / Actual'
				"""
			)

	frappe.db.commit()


def ensure_dispatch_scan_roles():
	"""Seed configurable dispatch-scan permissions without overwriting user changes."""
	if not frappe.db.exists("DocType", "Transport Settings") or not frappe.db.exists(
		"DocType", "Transport Scan Role"
	):
		return

	settings = frappe.get_single("Transport Settings")
	changed = False
	for fieldname, roles in DEFAULT_DISPATCH_SCAN_ROLES.items():
		configured_roles = {
			row.role for row in (getattr(settings, fieldname, None) or []) if row.role
		}
		if configured_roles and configured_roles != LEGACY_DEFAULT_DISPATCH_SCAN_ROLES[fieldname]:
			continue
		if configured_roles == LEGACY_DEFAULT_DISPATCH_SCAN_ROLES[fieldname]:
			settings.set(fieldname, [])
		for role in roles:
			if frappe.db.exists("Role", role):
				settings.append(fieldname, {"role": role})
				changed = True

	if changed:
		settings.save(ignore_permissions=True)
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
