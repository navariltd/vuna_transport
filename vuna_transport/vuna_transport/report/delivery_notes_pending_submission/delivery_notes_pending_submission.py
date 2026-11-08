# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	values = {field: filters.get(field) or None for field in ("company", "delivery_load")}
	rows = frappe.db.sql(
		"""select dn.name, dn.company, dn.customer, dn.posting_date,
			dn.vuna_delivery_load, dn.vuna_delivery_order, dn.vuna_transport_demand, dl.status as load_status
			from `tabDelivery Note` dn left join `tabTransport Load` dl
			on dl.name = dn.vuna_delivery_load
			where dn.docstatus = 0 and dn.is_return = 0
			and dn.vuna_delivery_load is not null and dn.vuna_delivery_load != ''
			and (%(company)s is null or dn.company = %(company)s)
			and (%(delivery_load)s is null or dn.vuna_delivery_load = %(delivery_load)s)
			order by dn.posting_date, dn.creation limit 500""",
		values,
		as_dict=True,
	)
	columns = [
		{
			"label": _("Transport Demand"),
			"fieldname": "vuna_transport_demand",
			"fieldtype": "Link",
			"options": "Transport Demand",
		},
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
