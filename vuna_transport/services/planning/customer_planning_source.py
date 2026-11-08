"""Explicit source dialect during the staged customer-demand cutover.

Read-only planner presentation keeps its date/address/quantity keys. Writes use
canonical Link fields; Transport Demand IDs never enter Delivery Order Links.
"""

import frappe
from frappe.utils import flt

from vuna_transport.services.demand.sales_transport_demand_service import native_customer_demand_enabled

ALIASES = {"required_delivery_date": "required_date", "delivery_address": "destination_address"}


def doctype():
	return "Transport Demand" if native_customer_demand_enabled() else "Delivery Order"


def field(name):
	return ALIASES.get(name, name) if native_customer_demand_enabled() else name


def fields(names):
	return [f"{field(name)} as {name}" if field(name) != name else name for name in names]


def filters(values):
	result = {field(key): value for key, value in values.items()}
	if native_customer_demand_enabled():
		result.update(
			movement_type="Customer Delivery", source_document_type="Sales Order", migration_archived=0
		)
		# Fully delivered/held sources can remain visible in execution, not in
		# new planning. Keep them out before live-balance validation.
		from vuna_transport.services.demand.sales_transport_demand_service import INACTIVE_SALES_STATUSES

		sales_filters = {"docstatus": 1, "status": ["not in", INACTIVE_SALES_STATUSES]}
		if result.get("company"):
			sales_filters["company"] = result["company"]
		open_sources = frappe.get_all(
			"Sales Order",
			filters=sales_filters,
			pluck="name",
		)
		selected = result.get("sales_order")
		if selected and isinstance(selected, (list, tuple)) and selected[0] == "in":
			open_sources = sorted(set(open_sources) & set(selected[1]))
		result["sales_order"] = ["in", open_sources]
	return result


def allocation_fields():
	return (
		("transport_demand", "transport_demand_item")
		if native_customer_demand_enabled()
		else ("delivery_order", "delivery_order_item")
	)


def metrics(names, include_items=False, source_warehouse=None):
	"""Bounded callers have already permission-filtered these demand parents.

	Re-use the live Sales Order balance checks used by load validation, not stale
	remaining caches. Source permissions and grouping/UOM checks still apply.
	"""
	from vuna_transport.services.planning.customer_demand_load_service import available_items

	result = {}
	for name in names:
		demand = frappe.get_doc("Transport Demand", name)
		demand.check_permission("read")
		values = available_items(demand)
		quantities = frappe.db.sql(
			"""select i.transport_demand_item,
		coalesce(sum(case when l.docstatus=0 then i.allocated_quantity else 0 end),0) reserved,
		coalesce(sum(case when l.docstatus=1 then i.allocated_quantity else 0 end),0) allocated
		from `tabTransport Load Item` i join `tabTransport Load` l on l.name=i.parent
		where i.transport_demand=%s and l.docstatus in (0,1) group by i.transport_demand_item""",
			name,
			as_dict=True,
		)
		by_item = {row.transport_demand_item: row for row in quantities}
		warehouse = source_warehouse or demand.source_warehouse
		if warehouse:
			location = frappe.get_doc("Warehouse", warehouse)
			location.check_permission("read")
			if location.company != demand.company or location.disabled or location.is_group:
				frappe.throw(frappe._("Select an active loading warehouse in the demand company."))
		bins = (
			{
				r.item_code: flt(r.actual_qty)
				for r in frappe.get_all(
					"Bin",
					filters={
						"warehouse": warehouse,
						"item_code": ["in", [r.item_code for r in demand.items]],
					},
					fields=["item_code", "actual_qty"],
				)
			}
			if warehouse
			else {}
		)
		totals = dict(
			requested_quantity=0.0,
			reserved_quantity=0.0,
			allocated_quantity=0.0,
			unallocated_quantity=0.0,
			items=[],
			**{d: 0.0 for d in ("weight", "volume", "pallets", "cases", "crates")},
		)
		for key, value in values.items():
			row = value["row"]
			remaining = value["available_quantity"]
			ratio = remaining / flt(row.transaction_qty) if row.transaction_qty else 0
			allocation = by_item.get(key, frappe._dict(reserved=0, allocated=0))
			for target, qty in (
				("requested_quantity", row.transaction_qty),
				("reserved_quantity", allocation.reserved),
				("allocated_quantity", allocation.allocated),
				("unallocated_quantity", remaining),
			):
				totals[target] += flt(qty)
			item = {
				"name": row.name,
				"item_code": row.item_code,
				"item_name": row.item_name,
				"transport_quantity": row.transaction_qty,
				"uom": row.transaction_uom,
				"conversion_factor": row.conversion_factor,
				"stock_uom": row.stock_uom,
				"stock_quantity": value["available_stock_qty"],
				"available_quantity": remaining,
				"reserved_quantity": allocation.reserved,
				"allocated_quantity": allocation.allocated,
				"stock_required_quantity": value["available_stock_qty"],
				"stock_available_quantity": bins.get(row.item_code, 0),
				"stock_availability_status": (
					"Available"
					if bins.get(row.item_code, 0) >= value["available_stock_qty"]
					else "Insufficient"
				)
				if warehouse
				else "Select Warehouse",
				"weight_uom": row.weight_uom,
				"volume_uom": row.volume_uom,
				"handling_requirements": row.transport_handling_profile,
			}
			for dimension in ("weight", "volume", "pallets", "cases", "crates"):
				item[dimension] = flt(row.get(dimension)) * ratio
				totals[dimension] += item[dimension]
			if include_items:
				totals["items"].append(item)
		result[name] = totals
	return result
