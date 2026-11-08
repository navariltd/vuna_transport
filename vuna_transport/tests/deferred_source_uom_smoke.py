"""Rollback-only check of depot requests whose fulfilment warehouse is unknown."""

import frappe
from frappe.utils import flt

from vuna_transport.services.demand.transport_demand_service import (
	build_source_legs,
	create_transport_demands,
)
from vuna_transport.services.execution.execution_service import get_load_execution_summary
from vuna_transport.services.fleet.allocation_cargo_service import collect_load_items
from vuna_transport.services.fleet.vehicle_allocation_service import make_vehicle_allocation_from_load
from vuna_transport.services.planning.transport_load_service import (
	create_transport_load_from_demands,
	get_committed_quantities,
)


def run():
	frappe.db.savepoint("deferred_source_uom")
	try:
		for name in frappe.get_all(
			"Material Request",
			filters={
				"docstatus": 1,
				"material_request_type": "Material Transfer",
				"status": ["not in", ["Stopped", "Closed", "Cancelled"]],
			},
			order_by="creation desc",
			pluck="name",
			limit_page_length=50,
		):
			source = frappe.get_doc("Material Request", name)
			legs = build_source_legs(source)
			if not any(not leg["source_warehouse"] for leg in legs):
				continue
			for demand_name in create_transport_demands("Material Request", name):
				demand = frappe.get_doc("Transport Demand", demand_name)
				if demand.source_warehouse:
					continue
				if demand.docstatus == 0:
					demand.submit()
				assert not demand.source_warehouse
				warehouse = frappe.db.get_value(
					"Warehouse",
					{
						"company": demand.company,
						"is_group": 0,
						"disabled": 0,
						"name": ["!=", demand.destination_warehouse],
					},
					"name",
				)
				assert warehouse
				item = demand.items[0]
				load = frappe.get_doc(
					"Transport Load",
					create_transport_load_from_demands(
						[
							{
								"transport_demand": demand.name,
								"transport_demand_item": item.name,
								"allocated_quantity": item.transaction_qty,
							}
						],
					),
				)
				row = load.delivery_orders[0]
				assert load.docstatus == 0 and not load.source_warehouse
				assert row.uom == item.transaction_uom
				assert flt(row.allocated_quantity) == flt(item.transaction_qty)
				assert abs(flt(row.allocated_stock_qty) - flt(item.stock_qty)) < 1e-6
				assert abs(get_committed_quantities([item.name])[item.name] - flt(item.stock_qty)) < 1e-6
				preview = collect_load_items([load])[0]
				assert preview["uom"] == item.transaction_uom
				assert preview["qty"] == flt(item.transaction_qty)
				try:
					load.submit()
				except frappe.ValidationError:
					pass
				else:
					raise AssertionError("Load submitted without a source warehouse")
				load.reload()
				load.source_warehouse = warehouse
				load.save()
				load.submit()
				summary = get_load_execution_summary(load.name)
				assert summary["planning_only"] and not summary["vehicle_allocation"]
				allocation = make_vehicle_allocation_from_load(load.name)
				assert allocation.delivery_loads[0].delivery_load == load.name
				return {
					"source_free_draft_load": "passed",
					"source_required_on_submission": "passed",
					"deferred_source": "passed",
					"requested_uom": item.transaction_uom,
					"quantity": item.transaction_qty,
					"stock_quantity": item.stock_qty,
					"allocate_vehicle_mapping": "passed",
					"rolled_back": True,
				}
		return {"result": "not exercised", "reason": "No submitted transfer request with an unknown source"}
	finally:
		frappe.db.rollback(save_point="deferred_source_uom")
