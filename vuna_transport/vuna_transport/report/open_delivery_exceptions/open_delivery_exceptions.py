# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = ["resolution_status in ('Open', 'In Resolution')"]
	values = {}
	for field in ("company", "customer", "delivery_load", "exception_type"):
		if filters.get(field):
			conditions.append(f"{field} = %({field})s")
			values[field] = filters[field]
	rows = frappe.db.sql(
		f"""select name, company, customer, delivery_load, delivery_trip,
			delivery_order, exception_type, exception_reason, custody,
			physical_resolution, commercial_resolution, total_unresolved_quantity,
			resolution_status, follow_up_date from `tabDelivery Exception`
			where {" and ".join(conditions)} order by follow_up_date, creation limit 500""",
		values,
		as_dict=True,
	)
	columns = [
		{
			"label": _("Delivery Exception"),
			"fieldname": "name",
			"fieldtype": "Link",
			"options": "Delivery Exception",
		},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company"},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer"},
		{
			"label": _("Delivery Load"),
			"fieldname": "delivery_load",
			"fieldtype": "Link",
			"options": "Delivery Load",
		},
		{
			"label": _("Delivery Order"),
			"fieldname": "delivery_order",
			"fieldtype": "Link",
			"options": "Delivery Order",
		},
		{"label": _("Type"), "fieldname": "exception_type", "fieldtype": "Data"},
		{"label": _("Reason"), "fieldname": "exception_reason", "fieldtype": "Data"},
		{"label": _("Unresolved Qty"), "fieldname": "total_unresolved_quantity", "fieldtype": "Float"},
		{"label": _("Follow-up"), "fieldname": "follow_up_date", "fieldtype": "Date"},
		{"label": _("Status"), "fieldname": "resolution_status", "fieldtype": "Data"},
	]
	return columns, rows
