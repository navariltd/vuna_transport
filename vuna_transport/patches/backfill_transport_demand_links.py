"""Add normalized lineage to unexecuted customer loads without rewriting cargo."""

import frappe
from frappe.utils import flt

from vuna_transport.services.demand.transport_demand_service import (
	build_source_legs,
	create_transport_demands,
)
from vuna_transport.services.planning.demand_lifecycle_service import sync_demand_allocations


def execute():
	result = {
		"linked_rows": 0,
		"skipped_loads_with_execution": 0,
		"skipped_sources": 0,
		"sources_requiring_review": [],
	}
	for name in frappe.get_all("Transport Load", filters={"docstatus": ["in", [0, 1]]}, pluck="name"):
		load = frappe.get_doc("Transport Load", name)
		if (
			load.delivery_trip
			or load.pick_list
			or frappe.db.exists("Delivery Note", {"vuna_delivery_load": name, "docstatus": ["!=", 2]})
		):
			result["skipped_loads_with_execution"] += 1
			continue
		orders = sorted(
			{
				row.delivery_order
				for row in load.delivery_orders
				if row.delivery_order and not row.get("transport_demand")
			}
		)
		for order_name in orders:
			source = frappe.get_doc("Delivery Order", order_name)
			if (
				source.docstatus != 1
				or source.status in ("Closed", "Cancelled")
				or not any(row.transport_qty > 0 for row in source.items)
			):
				result["skipped_sources"] += 1
				result["sources_requiring_review"].append(
					{"source": source.name, "reason": "Not eligible or no remaining transport quantity"}
				)
				continue
			if (
				not source.source_warehouse
				or not source.delivery_address
				or any(
					flt(row.conversion_factor) <= 0 or not row.uom or not row.stock_uom
					for row in source.items
				)
			):
				result["skipped_sources"] += 1
				result["sources_requiring_review"].append(
					{"source": source.name, "reason": "Missing endpoints or UOM conversion data"}
				)
				continue
			if not build_source_legs(source):
				continue
			for demand_name in create_transport_demands("Delivery Order", order_name):
				demand = frappe.get_doc("Transport Demand", demand_name)
				if demand.docstatus == 0:
					demand.submit()
				by_source = {item.source_item: item for item in demand.items}
				for row in load.delivery_orders:
					item = by_source.get(row.delivery_order_item)
					if row.delivery_order == order_name and item and not row.get("transport_demand"):
						frappe.db.set_value(
							"Transport Load Item",
							row.name,
							{"transport_demand": demand.name, "transport_demand_item": item.name},
							update_modified=False,
						)
						result["linked_rows"] += 1
				sync_demand_allocations([demand.name])
	return result
