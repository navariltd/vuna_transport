# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = ["dt.docstatus < 2"]
	values = {}
	for field in ("company", "status"):
		if filters.get(field):
			conditions.append(f"dt.{field} = %({field})s")
			values[field] = filters[field]
	rows = frappe.db.sql(f"""
		SELECT dt.name, dt.company, COALESCE(dt.vuna_current_vehicle, dt.vehicle) AS vehicle,
			COALESCE(dt.vuna_current_driver, dt.driver) AS driver,
			dt.vuna_delivery_load, dt.departure_time, dt.status
		FROM `tabDelivery Trip` dt
		WHERE {' AND '.join(conditions)}
		ORDER BY dt.departure_time DESC
		LIMIT 500
	""", values, as_dict=True)
	columns = [
		{"label": _("Delivery Trip"), "fieldname": "name", "fieldtype": "Link", "options": "Delivery Trip"},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company"},
		{"label": _("Vehicle"), "fieldname": "vehicle", "fieldtype": "Link", "options": "Vehicle"},
		{"label": _("Driver"), "fieldname": "driver", "fieldtype": "Link", "options": "Driver"},
		{"label": _("Delivery Load"), "fieldname": "vuna_delivery_load", "fieldtype": "Link", "options": "Delivery Load"},
		{"label": _("Departure"), "fieldname": "departure_time", "fieldtype": "Datetime"},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data"},
	]
	return columns, rows
