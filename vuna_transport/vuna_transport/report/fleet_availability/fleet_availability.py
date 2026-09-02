# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, now_datetime

from vuna_transport.services.fleet.vehicle_availability_service import get_vehicle_availability_map


def execute(filters: dict | None = None):
	"""Return columns and data for the report.

	This is the main entry point for the report. It accepts the filters as a
	dictionary and should return columns and data. It is called by the framework
	every time the report is refreshed or a filter is updated.
	"""
	filters = frappe._dict(filters or {})
	start = get_datetime(filters.from_datetime or now_datetime())
	end = get_datetime(filters.to_datetime or add_days(start, 1))
	columns = get_columns()
	data = get_data(filters, start, end)

	return columns, data


def execute_snapshot_report(filters: dict | None = None):
	"""Return columns and data for the report.

	This is the main entry point for snapshot report. When 'Synced
	Report' is enabled in report, framework will call this method
	every time the report is refreshed or a filter is updated. It
	accepts the same filters as normal execute. But a utility method -
	get_latest_sync, is also imported.

	"""
	from frappe.database.duckdb.database import get_latest_sync

	columns = get_columns()
	data = get_data(frappe._dict(filters or {}), now_datetime(), add_days(now_datetime(), 1))

	return columns, data


def get_columns() -> list[dict]:
	"""Return columns for the report.

	One field definition per column, just like a DocType field definition.
	"""
	return [
		{
			"label": _("Vehicle"),
			"fieldname": "vehicle",
			"fieldtype": "Link",
			"options": "Vehicle",
			"width": 130,
		},
		{"label": _("License Plate"), "fieldname": "license_plate", "fieldtype": "Data", "width": 120},
		{
			"label": _("Category"),
			"fieldname": "vehicle_category",
			"fieldtype": "Link",
			"options": "Vehicle Category",
			"width": 140,
		},
		{
			"label": _("Operational State"),
			"fieldname": "operational_state",
			"fieldtype": "Data",
			"width": 140,
		},
		{
			"label": _("Dispatch Eligible"),
			"fieldname": "dispatch_eligible",
			"fieldtype": "Check",
			"width": 110,
		},
		{"label": _("Primary Reason"), "fieldname": "primary_reason", "fieldtype": "Data", "width": 260},
		{
			"label": _("Current Allocation"),
			"fieldname": "current_allocation",
			"fieldtype": "Link",
			"options": "Vehicle Allocation",
			"width": 140,
		},
		{
			"label": _("Current Trip"),
			"fieldname": "current_trip",
			"fieldtype": "Link",
			"options": "Delivery Trip",
			"width": 140,
		},
	]


def get_data(filters, start, end):
	vehicle_names = frappe.get_all(
		"Vehicle",
		filters={"company": filters.company} if filters.company else {},
		pluck="name",
		limit_page_length=500,
	)
	results = get_vehicle_availability_map(
		vehicle_names=vehicle_names,
		from_time=start,
		to_time=end,
		company=filters.company,
		vehicle_category=filters.vehicle_category,
	)
	return [
		[
			result.vehicle,
			result.license_plate,
			result.vehicle_category,
			result.operational_state,
			int(result.dispatch_eligible),
			result.primary_reason,
			result.current_allocation,
			result.current_trip,
		]
		for result in results.values()
	]
