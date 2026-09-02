# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from vuna_transport.services.fleet.vehicle_compliance_service import get_vehicle_compliance_map


def execute(filters=None):
	filters = frappe._dict(filters or {})
	vehicles = frappe.get_all("Vehicle", filters={"company": filters.company} if filters.company else {}, fields=["name", "company"], limit_page_length=500)
	compliance = get_vehicle_compliance_map(vehicles)
	columns = [
		{"label": _("Vehicle"), "fieldname": "vehicle", "fieldtype": "Link", "options": "Vehicle"},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company"},
		{"label": _("Compliance Status"), "fieldname": "status", "fieldtype": "Data"},
		{"label": _("Dispatch Eligible"), "fieldname": "dispatch_eligible", "fieldtype": "Check"},
		{"label": _("Primary Reason"), "fieldname": "reason", "fieldtype": "Data", "width": 300},
	]
	data = [[v, next((x.company for x in vehicles if x.name == v), None), r.status, int(r.compliant), r.primary_reason] for v, r in compliance.items()]
	if filters.compliance_status:
		data = [row for row in data if row[2] == filters.compliance_status]
	return columns, data
