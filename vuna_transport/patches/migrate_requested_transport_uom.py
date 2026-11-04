"""Re-express transfer planning quantities in requested UOM without moving stock."""

import frappe
from frappe.utils import flt


def execute():
	from vuna_transport.services.fleet.allocation_cargo_service import collect_load_items
	from vuna_transport.services.fleet.vehicle_allocation_service import get_allocation_loads
	from vuna_transport.services.planning.demand_lifecycle_service import sync_demand_allocations

	loads, demands = set(), set()
	for row in frappe.get_all(
		"Transport Load Item",
		filters={"transport_demand": ["is", "set"], "delivery_order": ["is", "not set"]},
		fields=[
			"name",
			"parent",
			"transport_demand",
			"transport_demand_item",
			"allocated_quantity",
			"available_quantity",
			"allocated_stock_qty",
		],
	):
		if row.allocated_stock_qty:
			continue
		item = frappe.get_doc("Transport Demand Item", row.transport_demand_item)
		factor = flt(item.conversion_factor)
		if factor <= 0:
			frappe.throw(f"Repair UOM conversion for demand item {item.name} before migrating its load.")
		frappe.db.set_value(
			"Transport Load Item",
			row.name,
			{
				"allocated_stock_qty": row.allocated_quantity,
				"allocated_quantity": flt(row.allocated_quantity) / factor,
				"available_quantity": flt(row.available_quantity) / factor,
				"conversion_factor": factor,
				"uom": item.transaction_uom,
			},
			update_modified=False,
		)
		loads.add(row.parent)
		demands.add(row.transport_demand)
	for row in frappe.get_all("Transport Trip Task Item", fields=["name", "load_item", "qty", "stock_qty"]):
		if row.qty or not row.load_item:
			continue
		load_item = frappe.get_doc("Transport Load Item", row.load_item)
		if load_item.delivery_order:
			source = frappe.get_doc("Delivery Order Item", load_item.delivery_order_item)
			uom, factor = source.uom, flt(source.conversion_factor)
		else:
			source = frappe.get_doc("Transport Demand Item", load_item.transport_demand_item)
			uom, factor = source.transaction_uom, flt(source.conversion_factor)
		if uom and factor > 0:
			frappe.db.set_value(
				"Transport Trip Task Item",
				row.name,
				{"qty": flt(row.stock_qty) / factor, "uom": uom, "conversion_factor": factor},
				update_modified=False,
			)
	if loads:
		parents = frappe.get_all(
			"Vehicle Allocation Load", filters={"delivery_load": ["in", sorted(loads)]}, pluck="parent"
		)
		for name in set(parents):
			allocation = frappe.get_doc("Vehicle Allocation", name)
			if allocation.docstatus == 2:
				continue
			selected = get_allocation_loads(allocation)
			if any(load.docstatus == 2 for load in selected):
				continue
			items = collect_load_items(selected)
			frappe.db.delete("Vehicle Allocation Item", {"parent": name, "parenttype": "Vehicle Allocation"})
			for index, values in enumerate(items, start=1):
				frappe.get_doc(
					{
						"doctype": "Vehicle Allocation Item",
						"parent": name,
						"parenttype": "Vehicle Allocation",
						"parentfield": "loaded_items",
						"docstatus": allocation.docstatus,
						"idx": index,
						**values,
					}
				).db_insert()
	sync_demand_allocations(demands)
	return {"converted_loads": sorted(loads), "converted_demands": sorted(demands)}
