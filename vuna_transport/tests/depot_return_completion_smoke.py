"""Manual depot return -> real pickup/receipt -> inspection/transfer, always rolled back."""

import frappe


def run(vehicle_held=False, serialized=False):
	from vuna_transport.tests.task_trip_smoke import run as fixture

	previous = frappe.flags.phase6_completion_smoke
	frappe.flags.phase6_completion_smoke = (
		"Serialized" if serialized else "Vehicle" if vehicle_held else "Manual"
	)
	try:
		return fixture(verify_proof=True, depot_fraction=0 if vehicle_held else 1, verify_return_pickup=True)
	finally:
		frappe.flags.phase6_completion_smoke = previous


def serialized_request(request, trip):
	from vuna_transport.services.execution.manual_depot_return_service import get_depot_return_items
	from vuna_transport.tests.direct_return_pickup_smoke import exercise
	from vuna_transport.tests.task_trip_smoke import _add_fixture_quality_inspections

	item = frappe.copy_doc(frappe.get_doc("Item", request.items[0].item_code))
	item.item_code = "P6-SERIAL-" + frappe.generate_hash(length=8)
	item.item_name = item.item_code
	item.default_bom = None
	item.set("barcodes", [])
	item.has_batch_no, item.has_serial_no = 0, 1
	item.serial_no_series = "P6-.########"
	item.insert()
	serials = ["P6-" + frappe.generate_hash(length=12) for _ in range(4)]
	opening = frappe.new_doc("Stock Entry")
	opening.update(
		{"company": request.company, "stock_entry_type": "Material Receipt", "purpose": "Material Receipt"}
	)
	opening.append(
		"items",
		{
			"item_code": item.name,
			"qty": 4,
			"uom": item.stock_uom,
			"stock_uom": item.stock_uom,
			"conversion_factor": 1,
			"t_warehouse": request.source_warehouse,
			"basic_rate": 1,
			"serial_no": "\n".join(serials),
			"use_serial_batch_fields": 1,
		},
	)
	opening.insert()
	_add_fixture_quality_inspections(opening)
	opening.submit()
	manual = frappe.new_doc("Return Request")
	manual.update(
		{
			"return_context": "Depot",
			"company": request.company,
			"source_warehouse": request.source_warehouse,
			"source_stock_entry": opening.name,
			"return_warehouse": request.return_warehouse,
			"initiated_by": "Dispatcher",
		}
	)
	for row in get_depot_return_items(opening.name, request.source_warehouse):
		manual.append(
			"items",
			{**row, "requested_return_qty": row.delivered_qty, "return_reason": "Serialized rollback return"},
		)
	manual.insert()
	manual.submit()
	result = exercise(manual, trip)
	return {**result, "serialized_manual_pickup_and_receipt": "passed"}


def manual_request(request):
	from vuna_transport.services.execution.manual_depot_return_service import get_depot_return_items

	stock_name = frappe.db.get_value(
		"Stock Entry", {"vuna_transport_proof": request.proof_of_delivery, "docstatus": 1}, "name"
	)
	warehouse, destination, company = request.source_warehouse, request.return_warehouse, request.company
	request.cancel()
	manual = frappe.new_doc("Return Request")
	manual.update(
		{
			"return_context": "Depot",
			"company": company,
			"source_warehouse": warehouse,
			"source_stock_entry": stock_name,
			"return_warehouse": destination,
			"initiated_by": "Dispatcher",
		}
	)
	for row in get_depot_return_items(stock_name, warehouse):
		manual.append(
			"items",
			{
				**row,
				"requested_return_qty": row.delivered_qty,
				"return_reason": "Rollback later depot return",
			},
		)
	manual.insert()
	manual.submit()
	assert not manual.proof_of_delivery and not manual.customer
	return manual


def inspect_receipt(request, stock):
	from vuna_transport.services.execution.depot_return_inspection_service import create_inspection
	from vuna_transport.services.execution.return_v2_service import create_return_stock_entry
	from vuna_transport.tests.task_trip_smoke import _add_fixture_quality_inspections

	name = create_inspection(stock.name, request.name)
	assert create_inspection(stock.name, request.name) == name
	inspection = frappe.get_doc("Return Inspection", name)
	assert not inspection.sales_return and inspection.source_warehouse == request.return_warehouse
	parent = frappe.db.get_value("Warehouse", request.return_warehouse, "parent_warehouse")
	warehouse_type = frappe.db.get_value("Warehouse", request.return_warehouse, "warehouse_type")
	warehouses = [
		frappe.get_doc(
			{
				"doctype": "Warehouse",
				"warehouse_name": "Rollback inspected " + frappe.generate_hash(length=8),
				"company": request.company,
				"parent_warehouse": parent,
				"warehouse_type": warehouse_type,
			}
		)
		.insert()
		.name
		for _ in range(2)
	]
	# Test the independent result-group setting in unit tests. Stock-document QI
	# remains enabled here and is fulfilled with genuine fixture inspections.
	frappe.db.set_single_value("Transport Settings", "require_quality_inspection_for_return_inspection", 0)
	reason = frappe.db.get_value("Rejection Reason", {}, "name")
	assert reason, "A Rejection Reason is required for this smoke fixture"
	for row in list(inspection.results):
		row.inspection_qty /= 2
		row.target_warehouse = warehouses[0]
		other = row.as_dict()
		for field in ("name", "idx", "parent", "parenttype", "parentfield", "doctype"):
			other.pop(field, None)
		other.update(result="Rejected", target_warehouse=warehouses[1], reason=reason)
		inspection.append("results", other)
	inspection.save()
	inspection.submit()
	request.reload()
	assert request.status == "Awaiting Disposition"
	transfer = frappe.get_doc("Stock Entry", create_return_stock_entry(name))
	assert create_return_stock_entry(name) == transfer.name
	_add_fixture_quality_inspections(transfer)
	transfer.submit()
	request.reload()
	assert request.status == "Resolved"
	# ERPNext reverse links must block cancellation of the source receipt.
	frappe.db.savepoint("blocked_return_receipt_cancellation")
	try:
		stock.cancel()
	except (frappe.LinkExistsError, frappe.ValidationError):
		frappe.db.rollback(save_point="blocked_return_receipt_cancellation")
		stock.reload()
	else:
		raise AssertionError("Cannot cancel received stock beneath a submitted inspection")
	transfer.cancel()
	request.reload()
	assert request.status == "Awaiting Disposition"
	inspection.reload()
	inspection.cancel()
	request.reload()
	assert request.status == "Awaiting Inspection"
	return {
		"receipt_inspection_split": "passed",
		"batch_specific_redistribution": "passed",
		"inspection_cancellation_reopens_progress": "passed",
	}


def receive_vehicle(request):
	from vuna_transport.services.execution.depot_return_receiving_service import create_vehicle_receipt
	from vuna_transport.tests.task_trip_smoke import _add_fixture_quality_inspections

	name = create_vehicle_receipt(request.name)
	assert create_vehicle_receipt(request.name) == name
	stock = frappe.get_doc("Stock Entry", name)
	assert not stock.add_to_transit and stock.outgoing_stock_entry == request.source_stock_entry
	_add_fixture_quality_inspections(stock)
	stock.submit()
	request.reload()
	assert request.returned_qty == request.expected_return_qty
	checks = inspect_receipt(request, stock)
	stock.reload()
	stock.cancel()
	request.reload()
	assert request.returned_qty == 0
	return {**checks, "vehicle_held_recovery_without_pickup": "passed", "all_changes_rolled_back": True}
