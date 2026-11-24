# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder.functions import Coalesce, Date

from vuna_transport.services.shared.security_service import filter_readable_rows


def execute(filters=None):
	filters = frappe._dict(filters or {})
	allocation = frappe.qb.DocType("Vehicle Allocation")
	load = frappe.qb.DocType("Vehicle Allocation Load")
	effective_date = Coalesce(allocation.effective_from, allocation.creation)
	query = (
		frappe.qb.from_(allocation)
		.select(
			allocation.name,
			allocation.company,
			allocation.vehicle,
			allocation.driver,
			allocation.allocation_type,
			allocation.assignment_event,
			allocation.status,
			allocation.effective_from,
			allocation.effective_to,
			allocation.replacement_of,
			allocation.replaced_by,
		)
		.where(allocation.docstatus < 2)
		.orderby(effective_date, order=frappe.qb.desc)
		.limit(500)
	)
	for field in ("company", "vehicle"):
		if filters.get(field):
			query = query.where(allocation[field] == filters[field])
	if filters.from_date:
		query = query.where(Date(effective_date) >= filters.from_date)
	if filters.to_date:
		query = query.where(Date(effective_date) <= filters.to_date)
	rows = filter_readable_rows("Vehicle Allocation", query.run(as_dict=True))
	loads_by_allocation = {row["name"]: [] for row in rows}
	if rows:
		load_rows = (
			frappe.qb.from_(load)
			.select(load.parent, load.delivery_load)
			.where((load.parent.isin(list(loads_by_allocation))) & (load.parenttype == "Vehicle Allocation"))
			.orderby(load.parent, load.idx)
		).run(as_dict=True)
		for row in load_rows:
			names = loads_by_allocation[row.parent]
			if row.delivery_load and row.delivery_load not in names:
				names.append(row.delivery_load)
	for row in rows:
		row["delivery_load"] = ", ".join(loads_by_allocation[row["name"]]) or None
	columns = [
		{"label": _("Allocation"), "fieldname": "name", "fieldtype": "Link", "options": "Vehicle Allocation"},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company"},
		{"label": _("Vehicle"), "fieldname": "vehicle", "fieldtype": "Link", "options": "Vehicle"},
		{"label": _("Driver"), "fieldname": "driver", "fieldtype": "Link", "options": "Driver"},
		{
			"label": _("Transport Loads"),
			"fieldname": "delivery_load",
			"fieldtype": "Data",
		},
		{"label": _("Type"), "fieldname": "allocation_type", "fieldtype": "Data"},
		{"label": _("Event"), "fieldname": "assignment_event", "fieldtype": "Data"},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data"},
		{"label": _("From"), "fieldname": "effective_from", "fieldtype": "Datetime"},
		{"label": _("To"), "fieldname": "effective_to", "fieldtype": "Datetime"},
		{
			"label": _("Replaces"),
			"fieldname": "replacement_of",
			"fieldtype": "Link",
			"options": "Vehicle Allocation",
		},
	]
	return columns, rows
