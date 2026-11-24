# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder.functions import Coalesce

from vuna_transport.services.shared.security_service import filter_readable_rows


def execute(filters=None):
	filters = frappe._dict(filters or {})
	trip = frappe.qb.DocType("Delivery Trip")
	stop = frappe.qb.DocType("Delivery Stop")
	vehicle = Coalesce(trip.vuna_current_vehicle, trip.vehicle)
	driver = Coalesce(trip.vuna_current_driver, trip.driver)
	query = (
		frappe.qb.from_(trip)
		.join(stop)
		.on((stop.parent == trip.name) & (stop.parenttype == "Delivery Trip"))
		.select(
			trip.name,
			trip.company,
			vehicle.as_("vehicle"),
			driver.as_("driver"),
			stop.vuna_delivery_load,
			trip.departure_time,
			trip.status,
		)
		.where(trip.docstatus < 2)
		.groupby(
			trip.name,
			trip.company,
			vehicle,
			driver,
			stop.vuna_delivery_load,
			trip.departure_time,
			trip.status,
		)
		.orderby(trip.departure_time, order=frappe.qb.desc)
		.limit(500)
	)
	for field in ("company", "status"):
		if filters.get(field):
			query = query.where(trip[field] == filters[field])
	rows = query.run(as_dict=True)
	columns = [
		{"label": _("Delivery Trip"), "fieldname": "name", "fieldtype": "Link", "options": "Delivery Trip"},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company"},
		{"label": _("Vehicle"), "fieldname": "vehicle", "fieldtype": "Link", "options": "Vehicle"},
		{"label": _("Driver"), "fieldname": "driver", "fieldtype": "Link", "options": "Driver"},
		{
			"label": _("Transport Load"),
			"fieldname": "vuna_delivery_load",
			"fieldtype": "Link",
			"options": "Transport Load",
		},
		{"label": _("Departure"), "fieldname": "departure_time", "fieldtype": "Datetime"},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data"},
	]
	return columns, filter_readable_rows("Delivery Trip", rows)
