"""Derived demand allocation state and source cancellation policy."""

import frappe
from frappe import _
from frappe.utils import flt


def allocation_status(items):
	"""Compare each row independently; quantities in unlike UOMs are never summed."""
	if items and all(flt(row.allocated) >= flt(row.stock_qty) for row in items):
		return "Allocated"
	if items and all(flt(row.allocated) + flt(row.reserved) >= flt(row.stock_qty) for row in items):
		return "Reserved"
	if any(flt(row.allocated) or flt(row.reserved) for row in items):
		return "Partially Allocated"
	return "Open"


def lock_demands(names):
	if names:
		frappe.db.sql(
			"select name from `tabTransport Demand` where name in %(names)s order by name for update",
			{"names": tuple(sorted(set(names)))},
		)


def sync_demand_allocations(names):
	names = sorted(set(filter(None, names)))
	if not names:
		return
	lock_demands(names)
	from vuna_transport.services.demand.depot_return_demand_service import unpicked_releases

	releases = unpicked_releases()
	for name in names:
		demand = frappe.get_doc("Transport Demand", name)
		if demand.docstatus != 1:
			continue
		commitments = frappe.db.sql(
			"""
			select i.transport_demand_item,
			sum(case when l.docstatus = 0 then coalesce(nullif(i.allocated_stock_qty, 0), i.allocated_quantity * di.conversion_factor) else 0 end) as reserved,
			sum(case when l.docstatus = 1 then coalesce(nullif(i.allocated_stock_qty, 0), i.allocated_quantity * di.conversion_factor) else 0 end) as allocated
			from `tabTransport Load Item` i join `tabTransport Load` l on l.name = i.parent
			join `tabTransport Demand Item` di on di.name = i.transport_demand_item
			where i.transport_demand = %s and i.parenttype = 'Transport Load' and l.docstatus in (0, 1)
			group by i.transport_demand_item
		""",
			name,
			as_dict=True,
		)
		by_item = {row.transport_demand_item: row for row in commitments}
		states = []
		for item in demand.items:
			values = by_item.get(item.name, frappe._dict(reserved=0, allocated=0))
			values.allocated = max(flt(values.allocated) - flt(releases.get(item.name)), 0)
			frappe.db.set_value(
				"Transport Demand Item",
				item.name,
				{
					"reserved_stock_qty": flt(values.reserved),
					"allocated_stock_qty": flt(values.allocated),
					"remaining_stock_qty": max(
						flt(item.stock_qty) - flt(values.reserved) - flt(values.allocated), 0
					),
				},
				update_modified=False,
			)
			states.append(
				frappe._dict(stock_qty=item.stock_qty, reserved=values.reserved, allocated=values.allocated)
			)
		frappe.db.set_value(
			"Transport Demand", name, "status", allocation_status(states), update_modified=False
		)


def guard_source_cancellation(doc, method=None):
	"""Require explicit release/cancellation of demand before its source can cancel."""
	frappe.qb.from_(frappe.qb.DocType(doc.doctype)).select("name").where(
		frappe.qb.DocType(doc.doctype).name == doc.name
	).for_update().run()
	demands = frappe.get_all(
		"Transport Demand",
		filters={
			"source_document_type": doc.doctype,
			"source_document": doc.name,
			"docstatus": ["!=", 2],
		},
		pluck="name",
	)
	if doc.doctype == "Sales Order":
		orders = frappe.get_all("Delivery Order", filters={"sales_order": doc.name}, pluck="name")
		if orders:
			demands.extend(
				frappe.get_all(
					"Transport Demand",
					filters={
						"source_document_type": "Delivery Order",
						"source_document": ["in", orders],
						"docstatus": ["!=", 2],
					},
					pluck="name",
				)
			)
	lock_demands(demands)
	if demands:
		frappe.throw(
			_("Cancel linked Transport Demands before cancelling this source: {0}").format(", ".join(demands))
		)
