# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = {
		"docstatus": 1,
		"status": ["in", ["Approved", "Awaiting Vehicle"]],
		"active_vehicle_allocation": ["is", "not set"],
	}
	for field in (
		"company",
		"movement_type",
		"source_warehouse",
		"source_address",
		"required_vehicle_category",
	):
		if filters.get(field):
			conditions[field] = filters[field]
	rows = frappe.get_list(
		"Transport Load",
		filters=conditions,
		fields=[
			"name",
			"company",
			"movement_type",
			"delivery_method",
			"source_warehouse",
			"source_address",
			"dispatch_date",
			"dispatch_wave",
			"required_vehicle_category",
			"total_weight",
			"weight_uom",
			"total_volume",
			"volume_uom",
			"total_stops",
			"status",
		],
		order_by="dispatch_date, creation",
		limit=500,
	)
	rows = [
		row
		for row in rows
		if not row.delivery_method
		or frappe.get_cached_value("Delivery Method", row.delivery_method, "requires_vehicle_allocation")
	]
	columns = [
		{"label": _("Transport Load"), "fieldname": "name", "fieldtype": "Link", "options": "Transport Load"},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company"},
		{"label": _("Movement Type"), "fieldname": "movement_type", "fieldtype": "Data"},
		{
			"label": _("Warehouse"),
			"fieldname": "source_warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
		},
		{
			"label": _("Supplier Pickup Address"),
			"fieldname": "source_address",
			"fieldtype": "Link",
			"options": "Address",
		},
		{"label": _("Dispatch Date"), "fieldname": "dispatch_date", "fieldtype": "Date"},
		{"label": _("Wave"), "fieldname": "dispatch_wave", "fieldtype": "Data"},
		{
			"label": _("Required Category"),
			"fieldname": "required_vehicle_category",
			"fieldtype": "Link",
			"options": "Vehicle Category",
		},
		{"label": _("Weight"), "fieldname": "total_weight", "fieldtype": "Float"},
		{"label": _("Volume"), "fieldname": "total_volume", "fieldtype": "Float"},
		{"label": _("Stops"), "fieldname": "total_stops", "fieldtype": "Int"},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data"},
	]
	return columns, rows
