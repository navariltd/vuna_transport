# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from vuna_transport.services.fleet.vehicle_compliance_service import get_expiring_vehicle_documents


def execute(filters=None):
	filters = frappe._dict(filters or {})
	rows = get_expiring_vehicle_documents(filters.days or 30)
	if filters.company:
		rows = [row for row in rows if row.company == filters.company]
	columns = [
		{"label": _("Vehicle Document"), "fieldname": "name", "fieldtype": "Link", "options": "Vehicle Document"},
		{"label": _("Vehicle"), "fieldname": "vehicle", "fieldtype": "Link", "options": "Vehicle"},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company"},
		{"label": _("Document Type"), "fieldname": "document_type", "fieldtype": "Link", "options": "Vehicle Document Type"},
		{"label": _("Document Number"), "fieldname": "document_number", "fieldtype": "Data"},
		{"label": _("Expiry Date"), "fieldname": "expiry_date", "fieldtype": "Date"},
	]
	return columns, [list(row.values()) for row in rows]
