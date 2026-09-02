# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = ["docstatus = 1", "status in ('Open', 'Partially Allocated')"]
	values = {}
	for field in ("company", "source_warehouse", "transport_zone", "delivery_method", "priority", "customer"):
		if filters.get(field):
			conditions.append(f"{field} = %({field})s")
			values[field] = filters[field]
	if filters.get("from_date"):
		conditions.append("required_delivery_date >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append("required_delivery_date <= %(to_date)s")
		values["to_date"] = filters.to_date
	rows = frappe.db.sql(
		f"""
			select name, company, customer, source_warehouse, required_delivery_date,
				transport_zone, delivery_method, priority, requested_quantity,
				allocated_quantity, unallocated_quantity, total_weight, weight_uom,
				total_volume, volume_uom, status
			from `tabDelivery Order`
			where {" and ".join(conditions)}
			order by required_delivery_date, priority desc, creation
			limit 500
		""",
		values,
		as_dict=True,
	)
	columns = [
		{"label": _("Delivery Order"), "fieldname": "name", "fieldtype": "Link", "options": "Delivery Order"},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company"},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer"},
		{
			"label": _("Warehouse"),
			"fieldname": "source_warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
		},
		{"label": _("Required Date"), "fieldname": "required_delivery_date", "fieldtype": "Date"},
		{"label": _("Zone"), "fieldname": "transport_zone", "fieldtype": "Link", "options": "Transport Zone"},
		{"label": _("Priority"), "fieldname": "priority", "fieldtype": "Data"},
		{"label": _("Requested"), "fieldname": "requested_quantity", "fieldtype": "Float"},
		{"label": _("Allocated"), "fieldname": "allocated_quantity", "fieldtype": "Float"},
		{"label": _("Remaining"), "fieldname": "unallocated_quantity", "fieldtype": "Float"},
		{"label": _("Weight"), "fieldname": "total_weight", "fieldtype": "Float"},
		{"label": _("Volume"), "fieldname": "total_volume", "fieldtype": "Float"},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data"},
	]
	return columns, rows
