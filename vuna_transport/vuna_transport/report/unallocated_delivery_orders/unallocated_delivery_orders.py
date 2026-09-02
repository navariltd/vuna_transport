# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = ["docstatus = 1", "status in ('Open', 'Partially Allocated')", "unallocated_quantity > 0"]
	values = {}
	for field in ("company", "source_warehouse", "transport_zone", "customer"):
		if filters.get(field):
			conditions.append(f"{field} = %({field})s")
			values[field] = filters[field]
	rows = frappe.db.sql(
		f"""select name, company, customer, source_warehouse, required_delivery_date,
			transport_zone, requested_quantity, allocated_quantity, unallocated_quantity,
			total_weight, weight_uom, total_volume, volume_uom
			from `tabDelivery Order` where {" and ".join(conditions)}
			order by required_delivery_date, creation limit 500""",
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
		{"label": _("Requested"), "fieldname": "requested_quantity", "fieldtype": "Float"},
		{"label": _("Allocated"), "fieldname": "allocated_quantity", "fieldtype": "Float"},
		{"label": _("Remaining"), "fieldname": "unallocated_quantity", "fieldtype": "Float"},
		{"label": _("Weight"), "fieldname": "total_weight", "fieldtype": "Float"},
		{"label": _("Volume"), "fieldname": "total_volume", "fieldtype": "Float"},
	]
	return columns, rows
