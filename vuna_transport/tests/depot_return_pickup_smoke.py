"""Exercise depot return pickup inside task_trip_smoke's rollback transaction."""

from unittest.mock import patch

import frappe
from frappe.utils import now_datetime


def run(pickup_fraction=0.5):
	from vuna_transport.tests.task_trip_smoke import run as transfer_fixture

	previous = frappe.flags.return_pickup_smoke_fraction
	frappe.flags.return_pickup_smoke_fraction = pickup_fraction
	try:
		return transfer_fixture(verify_proof=True, depot_fraction=1, verify_return_pickup=True)
	finally:
		frappe.flags.return_pickup_smoke_fraction = previous


def exercise(request, original_trip):
	if frappe.flags.phase6_completion_smoke == "Serialized":
		from vuna_transport.tests.depot_return_completion_smoke import serialized_request

		return serialized_request(request, original_trip)
	if frappe.flags.phase6_completion_smoke == "Vehicle":
		from vuna_transport.tests.depot_return_completion_smoke import receive_vehicle

		return receive_vehicle(request)
	if frappe.flags.phase6_completion_smoke == "Manual":
		from vuna_transport.tests.depot_return_completion_smoke import manual_request

		request = manual_request(request)
	if frappe.flags.direct_return_pickup_smoke:
		from vuna_transport.tests.direct_return_pickup_smoke import exercise as direct_exercise

		return direct_exercise(request, original_trip)
	from erpnext.stock.doctype.stock_entry.stock_entry import StockEntry

	from vuna_transport.services.demand.transport_demand_service import create_transport_demands
	from vuna_transport.services.execution.transfer_proof_service import make_transfer_proof
	from vuna_transport.services.integrations.erpnext_integration import create_delivery_trip_for_load
	from vuna_transport.services.mobile.mobile_trip_execution_service import start_mobile_delivery_trip
	from vuna_transport.services.planning.transport_load_service import create_transport_load_from_demands
	from vuna_transport.tests.task_trip_smoke import _add_fixture_quality_inspections

	settings = frappe.get_doc("Transport Settings")
	settings.append(
		"demand_sources", {"source_document_type": "Return Request", "enabled": 1, "creation_mode": "Manual"}
	)
	settings.save()
	for warehouse in (request.source_warehouse, request.return_warehouse):
		from vuna_transport.services.routing.location_service import get_warehouse_planning_data

		if not get_warehouse_planning_data(warehouse).get("address"):
			frappe.get_doc(
				{
					"doctype": "Address",
					"address_title": "Rollback Return Pickup",
					"address_type": "Shipping",
					"address_line1": "Test",
					"city": "Test",
					"country": "Kenya",
					"links": [{"link_doctype": "Warehouse", "link_name": warehouse}],
				}
			).insert()
	demands = create_transport_demands("Return Request", request.name)
	assert len(demands) == 1
	assert create_transport_demands("Return Request", request.name) == demands
	demand = frappe.get_doc("Transport Demand", demands[0])
	demand.submit()
	load = frappe.get_doc(
		"Transport Load",
		create_transport_load_from_demands(
			[
				{
					"transport_demand": demand.name,
					"transport_demand_item": row.name,
					"allocated_quantity": row.transaction_qty,
				}
				for row in demand.items
			]
		),
	)
	load.submit()
	allocation = frappe.copy_doc(frappe.get_doc("Vehicle Allocation", original_trip.vuna_vehicle_allocation))
	allocation.docstatus, allocation.status, allocation.planned_reporting_time = (
		1,
		"Allocated",
		now_datetime(),
	)
	allocation.set("delivery_loads", [{"delivery_load": load.name}])
	allocation.db_insert()
	for row in allocation.delivery_loads:
		row.parent = allocation.name
		row.db_insert()
	load.db_set("active_vehicle_allocation", allocation.name)
	trip = create_delivery_trip_for_load(load.name)
	trip.insert()
	assert len(trip.delivery_stops) == 2
	trip.db_set("docstatus", 1, update_modified=False)
	with patch(
		"vuna_transport.services.mobile.mobile_trip_execution_service._require_mobile_driver",
		return_value=frappe._dict(name=trip.driver, full_name="Rollback Driver"),
	):
		start_mobile_delivery_trip("rollback-fixture", trip.name)
	trip.reload()
	tasks = frappe.get_all(
		"Transport Trip Task", filters={"delivery_trip": trip.name}, fields=["name", "task_type"]
	)
	pickup_task = next(task.name for task in tasks if task.task_type == "Return Pickup")
	receipt_task = next(task.name for task in tasks if task.task_type == "Transfer Receipt")
	try:
		make_transfer_proof(receipt_task)
	except frappe.ValidationError:
		pass
	else:
		raise AssertionError("Receiving must require a pickup first")
	pickup = make_transfer_proof(pickup_task)
	pickup_fraction = frappe.flags.return_pickup_smoke_fraction
	if pickup_fraction is None:
		pickup_fraction = 1 if frappe.flags.phase6_completion_smoke else 0.5
	pickup.received_by, pickup.remarks = "Depot Release Clerk", "Partial collection: remainder stays at depot"
	for row in pickup.items:
		row.delivered_qty *= pickup_fraction
	pickup.insert()
	stock_submit = StockEntry.submit

	def submit_inspected_fixture(entry):
		_add_fixture_quality_inspections(entry)
		return stock_submit(entry)

	if frappe.flags.phase6_completion_smoke:
		from vuna_transport.services.execution.return_stock_preparation_service import prepare_stock_entry

		prepared = frappe.get_doc("Stock Entry", prepare_stock_entry(pickup.name))
		assert prepared.docstatus == 0
		_add_fixture_quality_inspections(prepared)
		pickup.submit()
	else:
		with patch.object(StockEntry, "submit", submit_inspected_fixture):
			pickup.submit()
	if not pickup_fraction:
		assert not frappe.db.exists("Stock Entry", {"vuna_return_pickup_proof": pickup.name})
		assert frappe.db.get_value("Transport Trip Task", pickup_task, "status") == "Failed"
		assert frappe.db.get_value("Transport Trip Task", receipt_task, "status") == "Failed"
		return {
			"failed_pickup_posts_no_stock": "passed",
			"no_false_receipt": "passed",
			"all_changes_rolled_back": True,
		}
	assert frappe.db.get_value("Transport Trip Task", pickup_task, "status") == (
		"Completed" if pickup_fraction == 1 else "Completed with Variance"
	)
	request.reload()
	assert request.returned_qty == 0
	posted = frappe.get_doc("Stock Entry", {"vuna_return_pickup_proof": pickup.name, "docstatus": 1})
	assert posted.from_warehouse == request.source_warehouse
	assert all(row.uom == request.items[0].uom for row in posted.items)
	receipt = make_transfer_proof(receipt_task)
	receipt.received_by = "Receiving Returns Clerk"
	receipt.insert()
	if frappe.flags.phase6_completion_smoke:
		prepared = frappe.get_doc("Stock Entry", prepare_stock_entry(receipt.name))
		_add_fixture_quality_inspections(prepared)
		receipt.submit()
	else:
		with patch.object(StockEntry, "submit", submit_inspected_fixture):
			receipt.submit()
	request.reload()
	expected = sum(row.requested_return_qty for row in request.items)
	assert abs(request.returned_qty - expected * pickup_fraction) < 0.00001
	if pickup_fraction < 1:
		remaining_load = frappe.get_doc(
			"Transport Load",
			create_transport_load_from_demands(
				[
					{
						"transport_demand": demand.name,
						"transport_demand_item": row.name,
						"allocated_quantity": row.transaction_qty * (1 - pickup_fraction),
					}
					for row in demand.items
				]
			),
		)
		assert remaining_load.docstatus == 0
	else:
		assert create_transport_demands("Return Request", request.name) == demands
		assert request.status == "Awaiting Inspection"
	entry = frappe.get_doc("Stock Entry", {"vuna_transport_proof": receipt.name, "docstatus": 1})
	checks = {}
	if frappe.flags.phase6_completion_smoke:
		from vuna_transport.tests.depot_return_completion_smoke import inspect_receipt

		checks = inspect_receipt(request, entry)
	entry.cancel()
	request.reload()
	assert request.returned_qty == 0
	return {
		**checks,
		"depot_return_demand": "passed",
		"pickup_before_receipt": "passed",
		"partial_pickup_stock": "passed",
		"uncollected_replanning": "passed",
		"return_receipt_progress": "passed",
		"cancellation_reopens_return": "passed",
		"all_changes_rolled_back": True,
	}
