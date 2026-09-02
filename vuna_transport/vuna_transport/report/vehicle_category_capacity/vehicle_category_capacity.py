# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, now_datetime
from vuna_transport.services.fleet.vehicle_availability_service import get_vehicle_category_availability


def execute(filters=None):
	filters = frappe._dict(filters or {})
	start = get_datetime(filters.from_datetime or now_datetime())
	end = get_datetime(filters.to_datetime or add_days(start, 1))
	result = get_vehicle_category_availability(filters.company, start, end)
	columns = [
		{"label": _("Vehicle Category"), "fieldname": "vehicle_category", "fieldtype": "Link", "options": "Vehicle Category"},
		{"label": _("Total"), "fieldname": "total", "fieldtype": "Int"},
		{"label": _("Available"), "fieldname": "available", "fieldtype": "Int"},
		{"label": _("Allocated"), "fieldname": "allocated", "fieldtype": "Int"},
		{"label": _("Loading"), "fieldname": "loading", "fieldtype": "Int"},
		{"label": _("Ready for Dispatch"), "fieldname": "ready_for_dispatch", "fieldtype": "Int"},
		{"label": _("In Trip"), "fieldname": "in_trip", "fieldtype": "Int"},
		{"label": _("Unavailable"), "fieldname": "unavailable", "fieldtype": "Int"},
		{"label": _("Compliance Blocked"), "fieldname": "compliance_blocked", "fieldtype": "Int"},
	]
	fields = ["vehicle_category", "total", "available", "allocated", "loading", "ready_for_dispatch", "in_trip", "unavailable", "compliance_blocked"]
	return columns, [[summary.get(field, 0) for field in fields] for summary in result["categories"].values()]
