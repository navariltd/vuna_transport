# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = ["va.docstatus < 2"]
	values = {}
	for field in ("company", "vehicle"):
		if filters.get(field):
			conditions.append(f"va.{field} = %({field})s")
			values[field] = filters[field]
	if filters.from_date:
		conditions.append("DATE(COALESCE(va.effective_from, va.creation)) >= %(from_date)s")
		values.from_date = filters.from_date
	if filters.to_date:
		conditions.append("DATE(COALESCE(va.effective_from, va.creation)) <= %(to_date)s")
		values.to_date = filters.to_date
	rows = frappe.db.sql(f"""
		SELECT va.name, va.company, va.vehicle, va.driver, va.delivery_load,
			va.allocation_type, va.assignment_event, va.status,
			va.effective_from, va.effective_to, va.replacement_of, va.replaced_by
		FROM `tabVehicle Allocation` va
		WHERE {' AND '.join(conditions)}
		ORDER BY COALESCE(va.effective_from, va.creation) DESC
		LIMIT 500
	""", values, as_dict=True)
	columns = [
		{"label": _("Allocation"), "fieldname": "name", "fieldtype": "Link", "options": "Vehicle Allocation"},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company"},
		{"label": _("Vehicle"), "fieldname": "vehicle", "fieldtype": "Link", "options": "Vehicle"},
		{"label": _("Driver"), "fieldname": "driver", "fieldtype": "Link", "options": "Driver"},
		{"label": _("Delivery Load"), "fieldname": "delivery_load", "fieldtype": "Link", "options": "Delivery Load"},
		{"label": _("Type"), "fieldname": "allocation_type", "fieldtype": "Data"},
		{"label": _("Event"), "fieldname": "assignment_event", "fieldtype": "Data"},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data"},
		{"label": _("From"), "fieldname": "effective_from", "fieldtype": "Datetime"},
		{"label": _("To"), "fieldname": "effective_to", "fieldtype": "Datetime"},
		{"label": _("Replaces"), "fieldname": "replacement_of", "fieldtype": "Link", "options": "Vehicle Allocation"},
	]
	return columns, rows
