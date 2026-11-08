"""Rollback-only smoke check for an explicitly selected development site."""

import frappe

from vuna_transport.services.demand.transport_demand_service import (
	build_source_legs,
	create_transport_demands,
)
from vuna_transport.services.planning.demand_lifecycle_service import guard_source_cancellation
from vuna_transport.services.planning.transport_load_service import (
	create_transport_load_from_demands,
	get_committed_quantities,
	get_transfer_planning_items,
)


def run():
	"""Verify migrated metadata and, where possible, a real draft allocation."""
	result = {
		"transport_load_count": frappe.db.count("Transport Load"),
		"legacy_doctype_present": bool(frappe.db.exists("DocType", "Delivery Load")),
		"load_item_type": frappe.get_meta("Transport Load").get_field("delivery_orders").options,
		"naming_default": frappe.get_meta("Transport Load").get_field("naming_series").default,
	}
	assert not result["legacy_doctype_present"]
	assert result["load_item_type"] == "Transport Load Item"
	assert result["naming_default"].startswith("TL-")
	frappe.db.savepoint("transport_smoke")
	try:
		requests = frappe.get_all(
			"Material Request",
			filters={
				"docstatus": 1,
				"material_request_type": "Material Transfer",
				"status": ["not in", ["Stopped", "Closed", "Cancelled"]],
			},
			pluck="name",
			limit_page_length=50,
		)
		for name in requests:
			source = frappe.get_doc("Material Request", name)
			if not all(row.from_warehouse or source.get("set_from_warehouse") for row in source.items):
				continue
			if not build_source_legs(source):
				continue
			demand_names = create_transport_demands(source.doctype, source.name)
			assert create_transport_demands(source.doctype, source.name) == demand_names
			demand = frappe.get_doc("Transport Demand", demand_names[0])
			if demand.docstatus != 0:
				continue
			demand.submit()
			if frappe.conf.db_type == "mariadb":
				from frappe.database import get_db

				other = get_db(
					socket=frappe.db.socket,
					host=frappe.db.host,
					user=frappe.db.user,
					password=frappe.db.password,
					port=frappe.db.port,
					cur_db_name=frappe.db.cur_db_name,
				)
				try:
					other.sql("set session innodb_lock_wait_timeout = 1")
					try:
						other.sql(
							"select name from `tabTransport Demand` where name = %s for update", demand.name
						)
					except frappe.QueryTimeoutError:
						result["competing_connection_lock"] = "passed"
					else:
						raise AssertionError("Competing database session bypassed the demand row lock")
				finally:
					other.rollback()
					other.close()
			item = demand.items[0]
			load_name = create_transport_load_from_demands(
				[
					{
						"transport_demand": demand.name,
						"transport_demand_item": item.name,
						"allocated_quantity": item.transaction_qty,
					}
				]
			)
			assert get_committed_quantities([item.name])[item.name] == item.stock_qty
			item.reload()
			assert item.reserved_stock_qty == item.stock_qty
			assert item.remaining_stock_qty == 0
			try:
				create_transport_load_from_demands(
					[
						{
							"transport_demand": demand.name,
							"transport_demand_item": item.name,
							"allocated_quantity": 1,
						}
					]
				)
			except frappe.ValidationError:
				pass
			else:
				raise AssertionError("A second load over-allocated the reserved item")
			try:
				guard_source_cancellation(source)
			except frappe.ValidationError:
				pass
			else:
				raise AssertionError("Source cancellation bypassed active demand")
			frappe.delete_doc("Transport Load", load_name)
			assert not get_committed_quantities([item.name]).get(item.name)
			item.reload()
			assert item.reserved_stock_qty == 0
			assert item.remaining_stock_qty == item.stock_qty
			assert frappe.db.get_value("Transport Demand", demand.name, "status") == "Open"
			second_load = create_transport_load_from_demands(
				[
					{
						"transport_demand": demand.name,
						"transport_demand_item": item.name,
						"allocated_quantity": item.transaction_qty,
					}
				]
			)
			load = frappe.get_doc("Transport Load", second_load)
			load.submit()
			item.reload()
			assert item.allocated_stock_qty == item.stock_qty
			assert item.reserved_stock_qty == 0
			load.cancel()
			item.reload()
			assert item.allocated_stock_qty == 0
			assert item.remaining_stock_qty == item.stock_qty
			result["submission_and_cancellation"] = "passed (rolled back)"
			pool = get_transfer_planning_items(demand.company, source_warehouse=demand.source_warehouse)
			assert any(row["transport_demand_item"] == item.name for row in pool["items"])
			result["status_and_source_guard"] = "passed (rolled back)"
			result["draft_allocation_and_release"] = "passed (rolled back)"
			break
		else:
			result["draft_allocation_and_release"] = "not exercised: no eligible draftable source"
	finally:
		frappe.db.rollback(save_point="transport_smoke")
	return result
