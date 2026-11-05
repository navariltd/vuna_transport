"""Real direct assignment/pickup/receiving inside the parent fixture's rollback."""

from unittest.mock import patch
from uuid import uuid4

import frappe
from frappe.utils import now_datetime


def run():
	from vuna_transport.tests.task_trip_smoke import run as fixture

	previous = frappe.flags.direct_return_pickup_smoke
	frappe.flags.direct_return_pickup_smoke = True
	try:
		return fixture(verify_proof=True, depot_fraction=1, verify_return_pickup=True)
	finally:
		frappe.flags.direct_return_pickup_smoke = previous


def run_customer():
	from vuna_transport.api import prepare_mobile_transport_proof, submit_mobile_transport_proof
	from vuna_transport.services.execution.direct_return_pickup_service import (
		assign_return_pickup,
		remaining_items,
		remove_return_pickup,
	)
	from vuna_transport.services.execution.return_service import get_returnable_items

	try:
		for name in frappe.get_all(
			"Delivery Note", filters={"docstatus": 1, "is_return": 0}, pluck="name", limit_page_length=50
		):
			note = frappe.get_doc("Delivery Note", name)
			items = get_returnable_items(note.name)
			items = [
				row
				for row in items
				if row["requested_return_qty"] >= 1
				and not frappe.db.get_value("Item", row["item_code"], "has_serial_no")
				and (row["batch_no"] or not frappe.db.get_value("Item", row["item_code"], "has_batch_no"))
			]
			address = note.shipping_address_name or note.customer_address
			template = frappe.db.get_value(
				"Delivery Trip", {"docstatus": 1, "company": note.company, "driver": ["is", "set"]}, "name"
			)
			if items and address and template:
				break
		else:
			return {"result": "not exercised", "reason": "No eligible original sale and trip template"}
		request = frappe.new_doc("Return Request")
		request.update(
			{
				"customer": note.customer,
				"company": note.company,
				"delivery_note": note.name,
				"return_source": "Customer Return",
				"initiated_by": "Dispatcher",
				"return_warehouse": frappe.db.get_value(
					"Warehouse", {"company": note.company, "is_group": 0, "disabled": 0}, "name"
				),
			}
		)
		item = {**items[0], "requested_return_qty": 1, "return_reason": "Rollback customer collection"}
		request.append("items", item)
		request.insert()
		request.submit()
		trip = frappe.copy_doc(frappe.get_doc("Delivery Trip", template))
		trip.docstatus, trip.status, trip.vuna_task_based = 1, "In Transit", 0
		trip.vuna_actual_start, trip.vuna_actual_completion = now_datetime(), None
		trip.set("delivery_stops", [])
		trip.db_insert()
		stop = trip.append(
			"delivery_stops",
			{"docstatus": 1, "customer": note.customer, "address": address, "visited": 0, "idx": 1},
		)
		stop.db_insert()
		task_name = assign_return_pickup(request.name, trip.name, stop.name)
		stock_count = frappe.db.count("Stock Entry")
		with patch(
			"vuna_transport.services.mobile.mobile_device_service._require_mobile_driver",
			return_value=frappe._dict(name=trip.driver),
		):
			proof = prepare_mobile_transport_proof("rollback", task_name)
			assert proof["return_context"] == "Customer"
			result = submit_mobile_transport_proof(
				"rollback",
				task_name,
				{
					"request_id": str(uuid4()),
					"received_by": "Customer release contact",
					"items": [
						{
							"source_row": row["source_row"],
							"batch_no": row["batch_no"],
							"delivered_qty": row["qty"],
						}
						for row in proof["items"]
					],
				},
			)
		pod = frappe.get_doc("Proof of Delivery", result["name"])
		assert pod.docstatus == 1 and not pod.dispatch_stock_entry
		assert frappe.db.count("Stock Entry") == stock_count
		assert frappe.db.get_value("Transport Trip Task", task_name, "status") == "Completed"
		request.reload()
		assert not request.returned_qty and not request.return_stock_document and not request.credit_note
		assert remaining_items(request) == []
		pod.cancel()
		remove_return_pickup(task_name)
		return {
			"customer_direct_assignment": "passed",
			"mobile_collection": "passed",
			"no_stock_or_credit": "passed",
			"no_duplicate_collection": "passed",
			"all_changes_rolled_back": True,
		}
	finally:
		frappe.db.rollback()


def exercise(request, original_trip):
	from erpnext.stock.doctype.stock_entry.stock_entry import StockEntry, make_stock_in_entry

	from vuna_transport.api import prepare_mobile_transport_proof, submit_mobile_transport_proof
	from vuna_transport.services.execution.direct_return_pickup_service import (
		assign_return_pickup,
		get_assignment_stops,
		get_assignment_trip_query,
		remove_return_pickup,
	)
	from vuna_transport.services.mobile.mobile_device_service import get_delivery_trip_details
	from vuna_transport.services.mobile.mobile_trip_execution_service import start_mobile_delivery_trip
	from vuna_transport.services.routing.location_service import get_warehouse_planning_data
	from vuna_transport.tests.task_trip_smoke import _add_fixture_quality_inspections

	address = get_warehouse_planning_data(request.source_warehouse).get("address")
	if not address:
		address = (
			frappe.get_doc(
				{
					"doctype": "Address",
					"address_title": "Rollback Direct Pickup Depot",
					"address_type": "Shipping",
					"address_line1": "Test",
					"city": "Test",
					"country": "Kenya",
					"links": [{"link_doctype": "Warehouse", "link_name": request.source_warehouse}],
				}
			)
			.insert()
			.name
		)
	trip = frappe.copy_doc(original_trip)
	trip.docstatus, trip.status, trip.vuna_task_based = 1, "Scheduled", 0
	trip.vuna_actual_start = trip.vuna_actual_completion = None
	trip.set("delivery_stops", [])
	trip.db_insert()
	stop = trip.append("delivery_stops", {"docstatus": 1, "address": address, "visited": 0, "idx": 1})
	stop.db_insert()
	trip.reload()
	counts = {
		doctype: frappe.db.count(doctype)
		for doctype in ("Transport Demand", "Transport Load", "Vehicle Allocation")
	}
	assert any(row["value"] == stop.name for row in get_assignment_stops(request.name, trip.name))
	assert any(
		row[0] == trip.name
		for row in get_assignment_trip_query(
			"Delivery Trip", trip.name, "name", 0, 20, {"return_request": request.name}
		)
	)
	task_name = assign_return_pickup(request.name, trip.name, stop.name)
	assert assign_return_pickup(request.name, trip.name, stop.name) == task_name
	task = frappe.get_doc("Transport Trip Task", task_name)
	assert task.return_request == request.name and not task.transport_demand and not task.transport_load
	assert counts == {doctype: frappe.db.count(doctype) for doctype in counts}
	driver = frappe._dict(name=trip.driver, full_name="Rollback Assigned Driver")
	stock_submit = StockEntry.submit

	def inspected_fixture_stock(entry):
		_add_fixture_quality_inspections(entry)
		return stock_submit(entry)

	with (
		patch(
			"vuna_transport.services.mobile.mobile_device_service._require_mobile_driver", return_value=driver
		),
		patch(
			"vuna_transport.services.mobile.mobile_trip_execution_service._require_mobile_driver",
			return_value=driver,
		),
	):
		start_mobile_delivery_trip("rollback", trip.name)
		detail = get_delivery_trip_details("rollback", trip.name)
		assert any(operation.name == task_name for operation in detail["trip"]["stops"][0]["tasks"])
		proof = prepare_mobile_transport_proof("rollback", task_name)
		assert proof["items"] and all(row["uom"] == task.items[0].uom for row in proof["items"])
		payload = {
			"request_id": str(uuid4()),
			"received_by": "Depot release clerk",
			"remarks": "Partial collection",
			"items": [
				{
					"source_row": row["source_row"],
					"batch_no": row["batch_no"],
					"serial_no": "\n".join(
						(row.get("serial_no") or "").splitlines()[
							: int(row["qty"] * float(row.get("conversion_factor") or 1) / 2)
						]
					),
					"delivered_qty": row["qty"] / 2,
				}
				for row in proof["items"]
			],
		}
		with patch.object(StockEntry, "submit", inspected_fixture_stock):
			result = submit_mobile_transport_proof("rollback", task_name, payload)
		assert submit_mobile_transport_proof("rollback", task_name, payload) == result
	pod = frappe.get_doc("Proof of Delivery", result["name"])
	pickup = frappe.get_doc("Stock Entry", pod.dispatch_stock_entry)
	assert pickup.docstatus == 1 and pickup.add_to_transit and not pickup.vuna_transport_load
	task.reload()
	assert task.status == "Completed with Variance"
	assert frappe.db.get_value("Delivery Stop", stop.name, "visited") == 1
	request.reload()
	assert not request.returned_qty
	receipt = make_stock_in_entry(pickup.name)
	for row in receipt.items:
		row.t_warehouse = request.return_warehouse
	receipt.insert()
	_add_fixture_quality_inspections(receipt)
	receipt.submit()
	request.reload()
	assert abs(request.returned_qty - sum(row.delivered_qty for row in pod.items)) < 0.00001
	receipt.cancel()
	request.reload()
	assert not request.returned_qty
	pickup.reload()
	pickup.cancel()
	pod.cancel()
	remove_return_pickup(task.name)
	assert frappe.db.get_value("Transport Trip Task", task.name, "status") == "Cancelled"
	assert counts == {doctype: frappe.db.count(doctype) for doctype in counts}
	return {
		"direct_assignment": "passed",
		"no_planning_documents": "passed",
		"mobile_pickup": "passed",
		"partial_stock_and_receipt": "passed",
		"cancellation_and_release": "passed",
		"all_changes_rolled_back": True,
	}
