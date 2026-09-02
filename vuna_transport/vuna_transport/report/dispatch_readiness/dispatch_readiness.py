# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = ["dl.docstatus < 2"]
	values = {}
	for field in ("company", "status"):
		if filters.get(field):
			conditions.append(f"dl.{field} = %({field})s")
			values[field] = filters[field]
	if filters.from_date:
		conditions.append("DATE(dl.planned_departure) >= %(from_date)s")
		values.from_date = filters.from_date
	if filters.to_date:
		conditions.append("DATE(dl.planned_departure) <= %(to_date)s")
		values.to_date = filters.to_date
	rows = frappe.db.sql(f"""
		SELECT dl.name, dl.company, dl.status, dl.planned_departure, dl.planned_arrival,
			dl.required_vehicle_category, dl.total_weight, dl.total_volume,
			dl.total_stops,
			(SELECT va.name FROM `tabVehicle Allocation` va
			 WHERE va.delivery_load = dl.name AND va.docstatus < 2
			 ORDER BY va.creation DESC LIMIT 1) AS vehicle_allocation
		FROM `tabDelivery Load` dl
		WHERE {' AND '.join(conditions)}
		ORDER BY dl.planned_departure ASC
		LIMIT 500
	""", values, as_dict=True)
	columns = [
		{"label": _("Delivery Load"), "fieldname": "name", "fieldtype": "Link", "options": "Delivery Load"},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company"},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data"},
		{"label": _("Dispatch From"), "fieldname": "planned_departure", "fieldtype": "Datetime"},
		{"label": _("Dispatch To"), "fieldname": "planned_arrival", "fieldtype": "Datetime"},
		{"label": _("Required Category"), "fieldname": "required_vehicle_category", "fieldtype": "Link", "options": "Vehicle Category"},
		{"label": _("Weight"), "fieldname": "total_weight", "fieldtype": "Float"},
		{"label": _("Volume"), "fieldname": "total_volume", "fieldtype": "Float"},
		{"label": _("Stops"), "fieldname": "total_stops", "fieldtype": "Int"},
		{"label": _("Vehicle Allocation"), "fieldname": "vehicle_allocation", "fieldtype": "Link", "options": "Vehicle Allocation"},
	]
	return columns, rows
