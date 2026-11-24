# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder.functions import Count

from vuna_transport.services.shared.security_service import filter_readable_rows


def execute(filters=None):
	filters = frappe._dict(filters or {})
	trip = frappe.qb.DocType("Delivery Trip")
	stop = frappe.qb.DocType("Delivery Stop")
	fields = (trip.name, trip.company, trip.status, trip.vehicle, trip.driver, stop.vuna_delivery_load)
	query = (
		frappe.qb.from_(trip)
		.join(stop)
		.on((stop.parent == trip.name) & (stop.parenttype == "Delivery Trip"))
		.select(*fields, Count(stop.name).as_("pending_stops"))
		.where((trip.docstatus == 1) & stop.vuna_proof_of_delivery.isnull() & stop.delivery_note.notnull())
		.groupby(*fields, trip.creation)
		.orderby(trip.creation)
		.limit(500)
	)
	if filters.company:
		query = query.where(trip.company == filters.company)
	if filters.delivery_load:
		query = query.where(stop.vuna_delivery_load == filters.delivery_load)
	rows = query.run(as_dict=True)
	columns = [
		{"label": _("Delivery Trip"), "fieldname": "name", "fieldtype": "Link", "options": "Delivery Trip"},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company"},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data"},
		{"label": _("Vehicle"), "fieldname": "vehicle", "fieldtype": "Link", "options": "Vehicle"},
		{"label": _("Driver"), "fieldname": "driver", "fieldtype": "Link", "options": "Driver"},
		{
			"label": _("Transport Load"),
			"fieldname": "vuna_delivery_load",
			"fieldtype": "Link",
			"options": "Transport Load",
		},
		{"label": _("Pending Stops"), "fieldname": "pending_stops", "fieldtype": "Int"},
	]
	return columns, filter_readable_rows("Delivery Trip", rows)
