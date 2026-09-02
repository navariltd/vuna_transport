# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = ["vi.docstatus < 2"]
	values = {}
	for field in ("company", "vehicle", "resolution_status"):
		if filters.get(field):
			conditions.append(f"vi.{field} = %({field})s")
			values[field] = filters[field]
	if filters.get("vehicle_immobilized"):
		conditions.append("vi.vehicle_immobilized = 1")
	rows = frappe.db.sql(f"""
		SELECT vi.name, vi.company, vi.vehicle, vi.driver, vi.incident_type,
			vi.incident_datetime, vi.severity, vi.vehicle_immobilized,
			vi.resolution_status, vi.asset_repair, vi.replacement_vehicle_allocation
		FROM `tabVehicle Incident` vi
		WHERE {' AND '.join(conditions)}
		ORDER BY vi.incident_datetime DESC
		LIMIT 500
	""", values, as_dict=True)
	columns = [
		{"label": _("Incident"), "fieldname": "name", "fieldtype": "Link", "options": "Vehicle Incident"},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company"},
		{"label": _("Vehicle"), "fieldname": "vehicle", "fieldtype": "Link", "options": "Vehicle"},
		{"label": _("Driver"), "fieldname": "driver", "fieldtype": "Link", "options": "Driver"},
		{"label": _("Type"), "fieldname": "incident_type", "fieldtype": "Data"},
		{"label": _("Incident Datetime"), "fieldname": "incident_datetime", "fieldtype": "Datetime"},
		{"label": _("Severity"), "fieldname": "severity", "fieldtype": "Data"},
		{"label": _("Immobilized"), "fieldname": "vehicle_immobilized", "fieldtype": "Check"},
		{"label": _("Resolution Status"), "fieldname": "resolution_status", "fieldtype": "Data"},
		{"label": _("Asset Repair"), "fieldname": "asset_repair", "fieldtype": "Link", "options": "Asset Repair"},
		{"label": _("Replacement Allocation"), "fieldname": "replacement_vehicle_allocation", "fieldtype": "Link", "options": "Vehicle Allocation"},
	]
	return columns, rows
