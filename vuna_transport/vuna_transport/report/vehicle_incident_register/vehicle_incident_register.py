# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import cint

from vuna_transport.services.shared.security_service import filter_readable_rows


def execute(filters=None):
	filters = frappe._dict(filters or {})
	incident = frappe.qb.DocType("Vehicle Incident")
	query = (
		frappe.qb.from_(incident)
		.select(
			incident.name,
			incident.company,
			incident.vehicle,
			incident.driver,
			incident.incident_type,
			incident.incident_datetime,
			incident.severity,
			incident.vehicle_immobilized,
			incident.resolution_status,
			incident.asset_repair,
			incident.replacement_vehicle_allocation,
		)
		.where(incident.docstatus < 2)
		.orderby(incident.incident_datetime, order=frappe.qb.desc)
		.limit(500)
	)
	for field in ("company", "vehicle", "resolution_status"):
		if filters.get(field):
			query = query.where(incident[field] == filters[field])
	if cint(filters.get("vehicle_immobilized")):
		query = query.where(incident.vehicle_immobilized == 1)
	rows = query.run(as_dict=True)
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
		{
			"label": _("Asset Repair"),
			"fieldname": "asset_repair",
			"fieldtype": "Link",
			"options": "Asset Repair",
		},
		{
			"label": _("Replacement Allocation"),
			"fieldname": "replacement_vehicle_allocation",
			"fieldtype": "Link",
			"options": "Vehicle Allocation",
		},
	]
	return columns, filter_readable_rows("Vehicle Incident", rows)
