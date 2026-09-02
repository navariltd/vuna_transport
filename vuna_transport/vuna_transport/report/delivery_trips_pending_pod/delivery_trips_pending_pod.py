# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = ["dt.docstatus = 1", "ds.vuna_proof_of_delivery is null", "ds.delivery_note is not null"]
	values = {}
	if filters.company:
		conditions.append("dt.company = %(company)s")
		values.company = filters.company
	if filters.delivery_load:
		conditions.append("dt.vuna_delivery_load = %(delivery_load)s")
		values.delivery_load = filters.delivery_load
	rows = frappe.db.sql(
		f"""select dt.name, dt.company, dt.status, dt.vehicle, dt.driver,
			dt.vuna_delivery_load, count(ds.name) as pending_stops
			from `tabDelivery Trip` dt join `tabDelivery Stop` ds on ds.parent = dt.name
			where {" and ".join(conditions)} group by dt.name, dt.company, dt.status,
			dt.vehicle, dt.driver, dt.vuna_delivery_load order by dt.creation limit 500""",
		values,
		as_dict=True,
	)
	columns = [
		{"label": _("Delivery Trip"), "fieldname": "name", "fieldtype": "Link", "options": "Delivery Trip"},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company"},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data"},
		{"label": _("Vehicle"), "fieldname": "vehicle", "fieldtype": "Link", "options": "Vehicle"},
		{"label": _("Driver"), "fieldname": "driver", "fieldtype": "Link", "options": "Driver"},
		{
			"label": _("Delivery Load"),
			"fieldname": "vuna_delivery_load",
			"fieldtype": "Link",
			"options": "Delivery Load",
		},
		{"label": _("Pending Stops"), "fieldname": "pending_stops", "fieldtype": "Int"},
	]
	return columns, rows
