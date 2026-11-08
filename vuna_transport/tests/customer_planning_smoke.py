"""Exercise the real planner against isolated source fixtures; always rollback."""

from unittest.mock import patch

import frappe


def exercise(demand, warehouse):
	from vuna_transport.services.demand.delivery_order_migration import _fingerprint
	from vuna_transport.services.planning import dispatch_planning_service as planner
	from vuna_transport.services.planning.customer_demand_reports import execute
	from vuna_transport.services.planning.load_suggestion_service import (
		create_delivery_load_from_suggestion,
		suggest_delivery_loads,
	)

	frappe.db.savepoint("customer_planning_smoke")
	tracked = ("Transport Load", "Transport Load Item", "Stock Ledger Entry", "GL Entry", "Delivery Order")
	tracked = tuple(dt for dt in tracked if frappe.db.exists("DocType", dt))
	before = {dt: _fingerprint(dt) for dt in tracked}
	try:
		with patch.dict(frappe.conf, {"customer_demand_cutover": 1}):
			assert planner.get_customer_planning_context()["doctype"] == "Transport Demand"
			filters = [["Transport Demand", "name", "=", demand.name]]
			page = planner.get_planning_demand(
				demand.company, additional_filters=filters, source_warehouse=warehouse
			)
			assert [row["name"] for row in page["orders"]] == [demand.name], page
			details = planner.get_delivery_order_details([demand.name], source_warehouse=warehouse)[0]
			assert details["items"][0]["uom"] == demand.items[0].transaction_uom
			allocations = [
				{
					"transport_demand": demand.name,
					"transport_demand_item": demand.items[0].name,
					"allocated_quantity": 1,
				}
			]
			plan = planner.evaluate_manual_load_plan(allocations, source_warehouse=warehouse)
			assert plan["allocations"][0]["transport_demand_item"] == demand.items[0].name
			assert not plan["allocations"][0].get("delivery_order")
			frappe.db.savepoint("native_manual_plan")
			manual = frappe.get_doc(
				"Transport Load",
				planner.create_delivery_load_from_plan(allocations, source_warehouse=warehouse),
			)
			assert manual.delivery_orders[0].transport_demand_item == demand.items[0].name
			assert not manual.delivery_orders[0].delivery_order
			assert planner.get_delivery_order_details([demand.name])[0]["items"][0]["available_quantity"] == 3
			frappe.db.rollback(save_point="native_manual_plan")
			columns, rows = execute({"company": demand.company})
			assert any(
				row["name"] == demand.name and row["uom"] == demand.items[0].transaction_uom for row in rows
			)
			assert columns[0]["options"] == "Transport Demand"
			suggestions = suggest_delivery_loads(
				demand.company, delivery_order_names=[demand.name], source_warehouse=warehouse
			)
			assert suggestions["suggestions"], suggestions
			candidate = suggestions["suggestions"][0]
			assert candidate["allocations"][0]["transport_demand_item"] == demand.items[0].name
			assert candidate["source_warehouse"] == warehouse
			load = frappe.get_doc("Transport Load", create_delivery_load_from_suggestion(candidate))
			assert load.movement_type == "Customer Delivery" and not load.delivery_orders[0].delivery_order
			# Concurrent/stale browser acceptance must not reserve the same units twice.
			try:
				create_delivery_load_from_suggestion(candidate)
			except frappe.ValidationError:
				pass
			else:
				raise AssertionError("Stale suggestion over-reserved customer demand")
	finally:
		frappe.db.rollback(save_point="customer_planning_smoke")
	assert {dt: _fingerprint(dt) for dt in tracked} == before
