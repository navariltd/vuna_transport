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
	from vuna_transport.services.execution.driver_stock_policy import initialize_driver_stock_policy

	ensure_item_transport_requirement()
	ensure_transport_uoms()
	normalize_transport_cost_natures()
	ensure_transport_demand_sources()
	ensure_dispatch_scan_roles()
	ensure_delivery_methods()
	initialize_driver_stock_policy()


def ensure_item_transport_requirement():
	"""Install just this new field on upgrades; do not overwrite unrelated Item customizations."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	fixture = frappe.get_file_json(
		frappe.get_app_path("vuna_transport", "vuna_transport", "custom", "item.json")
	)
	field = next(row for row in fixture["custom_fields"] if row["fieldname"] == "vuna_requires_transport")
	# Exported record metadata is not part of the field definition. In particular,
	# an old `modified` value replaces the live timestamp and makes save() fail
	# Frappe's optimistic-lock check on an already-installed Custom Field.
	record_metadata = {"name", "creation", "modified", "modified_by", "owner", "docstatus", "doctype"}
	definition = {key: value for key, value in field.items() if key not in record_metadata}
	create_custom_fields({"Item": [definition]})


def ensure_transport_demand_sources():
	"""Initialize source policies before other setup helpers save the singleton.

	Keep configured rows intact and preserve legacy sales-generation choices.
	Returns and purchasing remain explicit opt-ins; new demand stays draft.
	"""
	settings = frappe.get_single("Transport Settings")
	if settings.get("demand_sources"):
		return
	settings.append(
		"demand_sources",
		{
			"source_document_type": "Sales Order",
			"enabled": 1,
			"creation_mode": "Automatic" if settings.get("auto_create_delivery_orders") else "Manual",
			"auto_submit_delivery_orders": settings.get("auto_submit_delivery_orders") or 0,
			"auto_submit_demand": 0,
		},
	)
	settings.append(
		"demand_sources",
		{
			"source_document_type": "Material Request",
			"enabled": 1,
			"creation_mode": "Manual",
			"auto_submit_demand": 0,
		},
	)
	settings.save(ignore_permissions=True)


def cleanup_retired_delivery_order_navigation():
	"""Remove stale retired links without overwriting sidebar customizations.

	Clean archived sidebars as well: subsequent framework conversion must not
	reintroduce links to removed DocTypes/reports. Never export site navigation
	back into the app's source files from this upgrade helper.
	"""
	if frappe.db.exists("DocType", "Delivery Order"):
		return 0
	retired_links = {
		("DocType", "Delivery Order"),
		("DocType", "Delivery Order Item"),
		("Report", "Open Delivery Demand"),
		("Report", "Unallocated Delivery Orders"),
	}
	removed = 0
	for parent_type, table_field in (
		("Sidebar", "items"),
		("Workspace Sidebar", "items"),
		("Custom Sidebar", "sidebar_items"),
		("Workspace", "links"),
	):
		if not frappe.db.exists("DocType", parent_type):
			continue
		meta = frappe.get_meta(parent_type)
		table = meta.get_field(table_field)
		if not table:
			continue
		scope = {"module": "Vuna Transport"} if parent_type == "Custom Sidebar" else {"app": "vuna_transport"}
		parents = frappe.get_all(parent_type, filters=scope, pluck="name")
		if not parents:
			continue
		rows = frappe.get_all(
			table.options,
			filters={"parent": ["in", parents], "parenttype": parent_type, "parentfield": table_field},
			fields=["name", "link_type", "link_to"],
		)
		obsolete = [row.name for row in rows if (row.link_type, row.link_to) in retired_links]
		if obsolete:
			frappe.db.delete(table.options, {"name": ["in", obsolete]})
			removed += len(obsolete)
	if removed:
		frappe.clear_cache()
	return removed


def ensure_delivery_methods():
	"""Seed standard policies. Custom names never determine workflow behavior."""
	for name, buying, selling, internal, required, arranger in (
		("Company Delivery", 0, 1, 0, 1, "Company"),
		("Customer Pickup", 0, 1, 0, 0, "Counterparty"),
		("Company Collection", 1, 0, 0, 1, "Company"),
		("Supplier Delivery", 1, 0, 0, 0, "Counterparty"),
		("Internal Transfer", 0, 0, 1, 1, "Company"),
		("Return Collection", 0, 1, 1, 0, "Company"),
		("Customer Return Drop-off", 0, 1, 0, 0, "Counterparty"),
	):
		if not frappe.db.exists("Delivery Method", name):
			frappe.get_doc(
				{
					"doctype": "Delivery Method",
					"delivery_method_name": name,
					"buying": buying,
					"selling": selling,
					"internal": internal,
					"transport_arranged_by": arranger,
					"requires_transport_planning": required,
					"requires_vehicle_allocation": required,
					"requires_delivery_trip": required,
				}
			).insert(ignore_permissions=True)


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
		configured_roles = {row.role for row in (getattr(settings, fieldname, None) or []) if row.role}
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
			territory = frappe.db.get_value("Territory", {"name": row.vuna_province, "is_group": 0}, "name")
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
			territory = frappe.db.get_value("Territory", {"name": row.province, "is_group": 0}, "name")
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
