# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	values = {field: filters.get(field) or None for field in ("company", "status", "from_date", "to_date")}
	rows = frappe.db.sql(
		"""
		SELECT dl.name, dl.company, dl.status, dl.planned_departure, dl.planned_arrival,
			dl.required_vehicle_category, dl.total_weight, dl.total_volume,
			dl.total_stops, dl.active_vehicle_allocation AS vehicle_allocation
		FROM `tabTransport Load` dl
		WHERE dl.docstatus < 2
		AND (%(company)s IS NULL OR dl.company = %(company)s)
		AND (%(status)s IS NULL OR dl.status = %(status)s)
		AND (%(from_date)s IS NULL OR DATE(dl.planned_departure) >= %(from_date)s)
		AND (%(to_date)s IS NULL OR DATE(dl.planned_departure) <= %(to_date)s)
		ORDER BY dl.planned_departure ASC
		LIMIT 500
	""",
		values,
		as_dict=True,
	)
	columns = [
		{"label": _("Transport Load"), "fieldname": "name", "fieldtype": "Link", "options": "Transport Load"},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company"},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data"},
		{"label": _("Dispatch From"), "fieldname": "planned_departure", "fieldtype": "Datetime"},
		{"label": _("Dispatch To"), "fieldname": "planned_arrival", "fieldtype": "Datetime"},
		{
			"label": _("Required Category"),
			"fieldname": "required_vehicle_category",
			"fieldtype": "Link",
			"options": "Vehicle Category",
		},
		{"label": _("Weight"), "fieldname": "total_weight", "fieldtype": "Float"},
		{"label": _("Volume"), "fieldname": "total_volume", "fieldtype": "Float"},
		{"label": _("Stops"), "fieldname": "total_stops", "fieldtype": "Int"},
		{
			"label": _("Vehicle Allocation"),
			"fieldname": "vehicle_allocation",
			"fieldtype": "Link",
			"options": "Vehicle Allocation",
		},
	]
	return columns, rows
