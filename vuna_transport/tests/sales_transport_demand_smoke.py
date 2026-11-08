"""Real direct customer demand + requested-UOM bundling; always rolled back."""

from unittest.mock import patch

import frappe

from vuna_transport.services.demand.sales_transport_demand_service import (
	create_sales_demands,
	refresh_sales_demands,
)
from vuna_transport.services.planning.customer_demand_load_service import get_load_items
from vuna_transport.services.planning.transport_load_service import create_transport_load_from_demands


def run(verify_execution=False, consolidated=False, without_trip=False, verify_planning=False):
	from vuna_transport.services.demand.delivery_order_migration import _fingerprint

	tracked = (
		"Delivery Order",
		"Delivery Order Item",
		"Transport Demand",
		"Transport Demand Item",
		"Transport Load",
		"Sales Order",
		"Sales Order Item",
		"Stock Ledger Entry",
		"GL Entry",
	)
	tracked = tuple(dt for dt in tracked if frappe.db.exists("DocType", dt))
	before = {dt: _fingerprint(dt) for dt in tracked}
	frappe.db.savepoint("direct_sales_smoke")
	try:
		for name in frappe.get_all(
			"Sales Order", filters={"docstatus": 1}, pluck="name", limit_page_length=100
		):
			template = frappe.get_doc("Sales Order", name)
			address = template.customer_address or template.shipping_address_name
			eligible = [
				r
				for r in template.items
				if r.warehouse
				and frappe.get_cached_value("Item", r.item_code, "is_stock_item")
				and not frappe.get_cached_value("Item", r.item_code, "disabled")
			]
			if address and eligible:
				break
		else:
			raise RuntimeError("No stock-item Sales Order/address template for smoke test")
		warehouse = eligible[0].warehouse
		source = frappe.copy_doc(template)
		source.name = "TEST-NATIVE-SALES-" + frappe.generate_hash(length=10)
		source.docstatus, source.status = 1, "To Deliver and Bill"
		source.vuna_delivery_method = "Company Delivery"
		source.set("items", [])
		row = frappe.copy_doc(eligible[0])
		item = frappe.get_cached_doc("Item", row.item_code)
		conversion = next((u for u in item.uoms if u.conversion_factor > 1), None)
		row.uom = conversion.uom if conversion else item.stock_uom
		row.conversion_factor = conversion.conversion_factor if conversion else 1
		row.qty, row.delivered_qty, row.stock_qty, row.warehouse = 4, 0, 4 * row.conversion_factor, None
		source.append("items", row)
		# Disposable submitted-state fixture, not a real sales commitment.
		source.db_insert()
		for child in source.get_all_children():
			child.docstatus, child.parent, child.parenttype = 1, source.name, "Sales Order"
			child.db_insert()
		policy = frappe._dict(
			enabled=True, creation_mode="Manual", auto_submit_demand=False, required_workflow_state=None
		)
		with patch(
			"vuna_transport.services.demand.source_policy_service.get_source_policy", return_value=policy
		):
			names = create_sales_demands(source.name)
			assert len(names) == 1 and names == create_sales_demands(source.name)
			demand = frappe.get_doc("Transport Demand", names[0])
			assert (
				demand.docstatus == 0
				and demand.source_document_type == "Sales Order"
				and not demand.source_warehouse
			)
			assert demand.items[0].source_item == source.items[0].name
			old_id = demand.items[0].name
			demand.save()
			assert demand.items[0].name == old_id
			demand.submit()
			if verify_planning:
				from vuna_transport.tests.customer_planning_smoke import exercise

				exercise(demand, warehouse)
			items = get_load_items(demand.name)
			assert len(items) == 1 and items[0]["available_quantity"] == 4 and items[0]["uom"] == row.uom
			items[0]["allocated_quantity"] = 2
			load = frappe.get_doc("Transport Load", create_transport_load_from_demands(items))
			assert load.docstatus == 0 and not load.source_warehouse
			assert load.movement_type == "Customer Delivery" and not load.delivery_orders[0].delivery_order
			assert load.delivery_orders[0].uom == row.uom
			assert load.delivery_orders[0].allocated_stock_qty == 2 * row.conversion_factor
			assert get_load_items(demand.name)[0]["available_quantity"] == 2
			frappe.db.set_value(
				"Sales Order Item",
				source.items[0].name,
				{"qty": 6, "stock_qty": 6 * row.conversion_factor},
				update_modified=False,
			)
			source.reload()
			refresh_sales_demands(source)
			demand.reload()
			assert demand.items[0].name == old_id and demand.items[0].transaction_qty == 6
			assert get_load_items(demand.name)[0]["available_quantity"] == 4
			frappe.db.set_value(
				"Sales Order Item",
				source.items[0].name,
				{"qty": 1, "stock_qty": row.conversion_factor},
				update_modified=False,
			)
			source.reload()
			try:
				refresh_sales_demands(source)
			except frappe.ValidationError as error:
				assert "Release reserved" in str(error)
			else:
				raise RuntimeError("Sales quantity reduced below reserved stock")
			frappe.db.set_value(
				"Sales Order Item",
				source.items[0].name,
				{"qty": 6, "stock_qty": 6 * row.conversion_factor},
				update_modified=False,
			)
			try:
				load.submit()
			except frappe.ValidationError as error:
				assert "Source Warehouse" in str(error)
			else:
				raise RuntimeError("Source-free customer load submitted")
			load.reload()
			load.source_warehouse = warehouse
			load.save()
			load.submit()
			assert load.docstatus == 1
			if "Delivery Order" in before:
				assert _fingerprint("Delivery Order") == before["Delivery Order"]
			assert _fingerprint("Stock Ledger Entry") == before["Stock Ledger Entry"]
			assert _fingerprint("GL Entry") == before["GL Entry"]
			result = {
				"planning_screens_verified": bool(verify_planning),
				"direct_sales_order_demand": True,
				"no_delivery_order_created": True,
				"idempotent": True,
				"uom": row.uom,
				"conversion_factor": row.conversion_factor,
				"draft_load_and_live_reservation": True,
				"source_quantity_refresh_and_reduction_guard": True,
				"native_load_submission": True,
			}
			if verify_execution:
				from vuna_transport.tests.native_customer_execution_smoke import exercise

				result.update(
					exercise(source, demand, load, consolidated=consolidated, without_trip=without_trip)
				)
	finally:
		frappe.db.rollback(save_point="direct_sales_smoke")
		# This isolated CLI rehearsal must not dispatch callbacks for rolled-back fixtures.
		frappe.db.after_commit.reset()
	assert {dt: _fingerprint(dt) for dt in tracked} == before
	result["rolled_back"] = True
	return result
