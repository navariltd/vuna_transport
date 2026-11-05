"""Real stock/custody integration check; every fixture is rolled back."""

from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.utils import now_datetime


def run(batched=False):
	from vuna_transport.services.demand.depot_return_demand_service import build_depot_return_legs
	from vuna_transport.services.execution.unplanned_pickup_service import (
		create_depot_case,
		make_collection_stock_entry,
		report_collection,
	)

	feature_flag = patch(
		"vuna_transport.services.execution.unplanned_pickup_policy.unplanned_pickups_enabled",
		return_value=True,
	)
	feature_flag.start()
	disabled_flag = None
	try:
		template = frappe.db.get_value("Delivery Trip", {"docstatus": 1, "driver": ["is", "set"]}, "name")
		if not template:
			return {"result": "not exercised", "reason": "No trip template"}
		trip = frappe.copy_doc(frappe.get_doc("Delivery Trip", template))
		trip.set("delivery_stops", [])
		trip.docstatus, trip.status = 1, "In Transit"
		trip.vuna_actual_start, trip.vuna_actual_completion = now_datetime(), None
		trip.db_insert()
		stop = trip.append("delivery_stops", {"docstatus": 1})
		stop.db_insert()
		parent = frappe.db.get_value("Warehouse", {"company": trip.company, "is_group": 1}, "name")
		tag = frappe.generate_hash(length=8)
		warehouse_type = frappe.db.get_value("Warehouse Type", {"name": ["!=", "Transit"]}, "name")
		warehouses = []
		for label in ("Source", "Holding"):
			warehouse = frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": f"Rollback Collection {label} {tag}",
					"company": trip.company,
					"parent_warehouse": parent,
					"warehouse_type": warehouse_type,
				}
			)
			warehouse.insert()
			warehouses.append(warehouse.name)
		address = frappe.get_doc(
			{
				"doctype": "Address",
				"address_title": f"Rollback Collection {tag}",
				"address_type": "Shipping",
				"address_line1": "Test depot",
				"city": "Test",
				"country": "Kenya",
				"links": [{"link_doctype": "Warehouse", "link_name": warehouses[0]}],
			}
		).insert()
		stop.db_set("address", address.name)
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": f"ROLLBACK-COLLECTION-{tag}",
				"item_name": "Rollback Collection Item",
				"item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
				"stock_uom": "Pcs",
				"is_stock_item": 1,
				"item_defaults": [
					{
						"company": trip.company,
						"default_inventory_account": frappe.db.get_value(
							"Account",
							{"company": trip.company, "account_type": "Stock", "is_group": 0, "disabled": 0},
							"name",
						),
					}
				],
				"has_batch_no": int(batched),
				"uoms": [{"uom": "Pcs", "conversion_factor": 1}, {"uom": "Carton", "conversion_factor": 10}],
			}
		)
		item.insert()
		if "nl_vms" in frappe.get_installed_apps():
			from nl_vms.controllers.warehouse_type import (
				ALLOWED_WAREHOUSE_TYPE_DOCTYPE,
				ITEM_WAREHOUSE_TYPES_FIELD,
			)

			frappe.get_doc(
				{
					"doctype": ALLOWED_WAREHOUSE_TYPE_DOCTYPE,
					"parent": item.name,
					"parenttype": "Item",
					"parentfield": ITEM_WAREHOUSE_TYPES_FIELD,
					"warehouse_type": warehouse_type,
				}
			).db_insert()
		batch = None
		if batched:
			batch = (
				frappe.get_doc({"doctype": "Batch", "batch_id": f"ROLLBACK-{tag}", "item": item.name})
				.insert()
				.name
			)
			second_batch = (
				frappe.get_doc({"doctype": "Batch", "batch_id": f"ROLLBACK-SECOND-{tag}", "item": item.name})
				.insert()
				.name
			)
		seed = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"company": trip.company,
				"items": [
					{
						"item_code": item.name,
						"qty": 2,
						"uom": "Carton",
						"stock_uom": "Pcs",
						"conversion_factor": 10,
						"t_warehouse": warehouses[0],
						"basic_rate": 10,
						"batch_no": batch,
						"use_serial_batch_fields": 1,
					}
				],
			}
		)
		if batched:
			second_row = seed.append("items", seed.items[0].as_dict())
			second_row.name = None
			second_row.batch_no = second_batch
		seed.insert()
		seed.submit()
		payload = {
			"reference_doctype": "Stock Entry",
			"reference_name": seed.name,
			"stop_name": stop.name,
			"request_id": str(uuid4()),
			"party_type": "Depot",
			"location": "Rollback depot",
			"released_by": "Depot clerk",
			"reason": "Unplanned return",
			"items": [{"item_code": item.name, "delivered_qty": 2, "uom": "Carton", "batch_no": batch}],
		}
		if batched:
			payload["items"][0]["delivered_qty"] = 1
			payload["items"].append(
				{"item_code": item.name, "delivered_qty": 1, "uom": "Carton", "batch_no": second_batch}
			)
		for index, row in enumerate(payload["items"]):
			row["collection_reference_row"] = seed.items[index].name
		with patch(
			"vuna_transport.services.mobile.mobile_trip_execution_service._lock_assigned_trip",
			return_value=(frappe._dict(name=trip.driver), trip),
		):
			result = report_collection("rollback", trip.name, payload)
			assert report_collection("rollback", trip.name, payload) == {
				"name": result["name"],
				"created": False,
			}
		proof = frappe.get_doc("Proof of Delivery", result["name"])
		assert proof.docstatus == 1 and not proof.delivery_stop and not proof.transport_trip_task
		assert proof.collection_stop == stop.name
		assert (
			all(row.uom == "Carton" for row in proof.items)
			and sum(row.stock_qty for row in proof.items) == 20
		)
		assert not frappe.db.exists("Stock Entry", {"vuna_unplanned_collection": proof.name})
		feature_flag.stop()
		disabled_flag = patch(
			"vuna_transport.services.execution.unplanned_pickup_policy.unplanned_pickups_enabled",
			return_value=False,
		)
		disabled_flag.start()
		with patch(
			"vuna_transport.services.mobile.mobile_trip_execution_service._lock_assigned_trip",
			return_value=(frappe._dict(name=trip.driver), trip),
		):
			assert report_collection("rollback", trip.name, payload)["name"] == proof.name
			try:
				report_collection("rollback", trip.name, {**payload, "request_id": str(uuid4())})
			except frappe.ValidationError:
				pass
			else:
				raise AssertionError("Disabling the setting must block new collection reports")
		case_name = create_depot_case(
			proof.name, warehouses[0], warehouses[1], "Physical receipt by warehouse staff required"
		)
		case = frappe.get_doc("Return Request", case_name)
		assert case.docstatus == 0 and sum(row.requested_return_qty for row in case.items) == 2
		case.submit()
		assert build_depot_return_legs(case) == []
		stock = make_collection_stock_entry(proof.name)
		assert stock.docstatus == 0 and stock.items[0].uom == "Carton"
		from vuna_transport.services.execution.unplanned_pickup_service import validate_collection_stock

		stock.items[0].qty += 1
		try:
			validate_collection_stock(stock)
		except frappe.ValidationError:
			pass
		else:
			raise AssertionError("Inflated collection receipt must be rejected")
		stock.items[0].qty -= 1
		stock.insert()
		stock.submit()
		case.reload()
		assert case.returned_qty == 2 and case.status == "Awaiting Inspection"
		assert (
			frappe.db.get_value("Bin", {"item_code": item.name, "warehouse": warehouses[1]}, "actual_qty")
			== 20
		)
		stock.cancel()
		case.reload()
		assert case.returned_qty == 0 and case.status == "Awaiting Receipt"
		case.cancel()
		proof.reload()
		assert not proof.return_request and proof.collection_review_status == "Pending Review"
		proof.cancel()
		return {
			"result": "passed",
			"batched": batched,
			"checks": "evidence, idempotency, review, draft case, no demand, stock receipt, cancellation",
		}
	finally:
		if disabled_flag:
			disabled_flag.stop()
		feature_flag.stop()
		frappe.db.rollback()
