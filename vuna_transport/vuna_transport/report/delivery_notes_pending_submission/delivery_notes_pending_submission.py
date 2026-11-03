# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = ["dn.docstatus = 0", "dn.vuna_delivery_load is not null", "dn.vuna_delivery_load != ''"]
	values = {}
	if filters.company:
		conditions.append("dn.company = %(company)s")
		values.company = filters.company
	if filters.delivery_load:
		conditions.append("dn.vuna_delivery_load = %(delivery_load)s")
		values.delivery_load = filters.delivery_load
	rows = frappe.db.sql(
		f"""select dn.name, dn.company, dn.customer, dn.posting_date,
			dn.vuna_delivery_load, dn.vuna_delivery_order, dl.status as load_status
			from `tabDelivery Note` dn left join `tabTransport Load` dl
			on dl.name = dn.vuna_delivery_load where {" and ".join(conditions)}
			order by dn.posting_date, dn.creation limit 500""",
		values,
		as_dict=True,
	)
	columns = [
		{"label": _("Delivery Note"), "fieldname": "name", "fieldtype": "Link", "options": "Delivery Note"},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company"},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer"},
		{
			"label": _("Transport Load"),
			"fieldname": "vuna_delivery_load",
			"fieldtype": "Link",
			"options": "Transport Load",
		},
		{
			"label": _("Delivery Order"),
			"fieldname": "vuna_delivery_order",
			"fieldtype": "Link",
			"options": "Delivery Order",
		},
		{"label": _("Load Status"), "fieldname": "load_status", "fieldtype": "Data"},
	]
	return columns, rows
