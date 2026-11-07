"""Submitted-state supplier PO -> demand. Fixtures and all writes roll back."""

from unittest.mock import patch

import frappe


def run(verify_vehicle_stock=False, batched=False, partial_transfer=False):
	from vuna_transport.services.demand.transport_demand_service import create_transport_demands
	from vuna_transport.services.planning.demand_lifecycle_service import guard_source_cancellation

	previous_in_test = frappe.flags.in_test
	frappe.flags.in_test = True
	try:
		template = None
		for name in frappe.get_all(
			"Purchase Order", filters={"docstatus": 1}, pluck="name", limit_page_length=100
		):
			candidate = frappe.get_doc("Purchase Order", name)
			if candidate.get("is_subcontracted") or candidate.get("has_unit_price_items"):
				continue
			rows = [
				row
				for row in candidate.items
				if row.warehouse
				and not row.get("delivered_by_supplier")
				and frappe.get_cached_value("Item", row.item_code, "is_stock_item")
				and (not batched or frappe.get_cached_value("Item", row.item_code, "has_batch_no"))
				and (
					not verify_vehicle_stock
					or not frappe.get_cached_value("Item", row.item_code, "has_serial_no")
				)
			]
			if rows:
				template, source_row = candidate, rows[0]
				break
		if not template:
			return {
				"result": "not exercised",
				"reason": "No submitted stock-item PO template with a receiving warehouse",
			}
		ledger_counts = {
			doctype: frappe.db.count(doctype)
			for doctype in ("Stock Ledger Entry", "GL Entry", "Purchase Receipt", "Stock Entry")
		}
		address = frappe.get_doc(
			{
				"doctype": "Address",
				"address_title": "Rollback Supplier Pickup",
				"address_type": "Shipping",
				"address_line1": "Test supplier gate",
				"city": "Test",
				"country": "Kenya",
				"links": [{"link_doctype": "Supplier", "link_name": template.supplier}],
			}
		).insert()
		source = frappe.copy_doc(template)
		source.name = "TEST-PICKUP-" + frappe.generate_hash(length=12)
		source.docstatus, source.status = 1, "To Receive and Bill"
		source.vuna_company_pickup, source.vuna_pickup_address = 1, address.name
		source.set("items", [])
		row = frappe.copy_doc(source_row)
		row.qty, row.received_qty = 20, 5
		row.stock_qty = row.qty * row.conversion_factor
		row.schedule_date = "2026-11-10"
		source.append("items", row)
		# Avoid submitting a real purchase commitment or notifying a supplier.
		# The adapter sees a real, disposable submitted-state source document.
		source.db_insert()
		for child in source.get_all_children():
			child.docstatus = 1
			child.parent, child.parenttype = source.name, "Purchase Order"
			child.db_insert()
		policy = frappe._dict(
			enabled=True, creation_mode="Manual", auto_submit_demand=False, required_workflow_state=None
		)
		with patch(
			"vuna_transport.services.demand.source_policy_service.get_source_policy", return_value=policy
		):
			names = create_transport_demands("Purchase Order", source.name)
			assert names == create_transport_demands("Purchase Order", source.name)
			demand = frappe.get_doc("Transport Demand", names[0])
			assert demand.docstatus == 0 and demand.movement_type == "Supplier Pickup"
			assert demand.supplier == source.supplier and demand.source_address == address.name
			assert not demand.source_warehouse and not demand.customer
			assert demand.items[0].transaction_qty == 15
			assert demand.items[0].transaction_uom == row.uom
			assert demand.items[0].stock_qty == 15 * row.conversion_factor
			demand.submit()
		try:
			guard_source_cancellation(source)
		except frappe.ValidationError:
			pass
		else:
			raise AssertionError("An active demand must block cancellation of its source")
		if verify_vehicle_stock:
			with patch(
				"vuna_transport.services.demand.source_policy_service.get_source_policy", return_value=policy
			):
				return exercise_vehicle_stock(source, row, partial_transfer=partial_transfer)
		assert ledger_counts == {doctype: frappe.db.count(doctype) for doctype in ledger_counts}
		return {
			"supplier_demand": "passed",
			"requested_uom": row.uom,
			"idempotency": "passed",
			"source_cancellation_guard": "passed",
			"no_stock_or_financial_documents": "passed",
			"all_changes_rolled_back": True,
		}
	finally:
		frappe.db.rollback()
		frappe.flags.in_test = previous_in_test


def exercise_vehicle_stock(order, source_row, partial_transfer=False):
	from vuna_transport.services.execution.supplier_vehicle_receipt_service import (
		create_vehicle_purchase_receipt,
	)
	from vuna_transport.services.execution.supplier_vehicle_transfer_service import (
		create_vehicle_material_transfer,
	)
	from vuna_transport.tests.task_trip_smoke import _add_fixture_quality_inspections

	# The demand-only fixture's historical received=5 has no physical PR behind
	# it. For real ledger checks use a fresh 15-unit PO with received=0: ERPNext
	# correctly recomputes received_qty from actual posted purchase documents.
	source_row.qty, source_row.received_qty = 15, 0
	source_row.stock_qty = 15 * source_row.conversion_factor
	source_row.db_update()
	order.reload()
	allocation = None
	for name in frappe.get_all(
		"Vehicle Allocation",
		filters={
			"docstatus": 1,
			"company": order.company,
			"status": ["not in", ["Completed", "Replaced", "Cancelled"]],
		},
		pluck="name",
	):
		candidate = frappe.get_doc("Vehicle Allocation", name)
		if (
			candidate.vehicle
			and candidate.driver
			and frappe.db.count(
				"Warehouse",
				{
					"vehicle": candidate.vehicle,
					"warehouse_type": "Transit",
					"company": candidate.company,
					"is_group": 0,
					"disabled": 0,
				},
			)
			== 1
		):
			allocation = candidate
			break
	if not allocation:
		raise AssertionError(
			"No eligible active allocation with exactly one linked vehicle transit warehouse"
		)
	# Legitimate test-only compatibility; never bypass validation or change live policy.
	if "nl_vms" in frappe.get_installed_apps():
		from nl_vms.controllers.warehouse_type import (
			ALLOWED_WAREHOUSE_TYPE_DOCTYPE,
			ITEM_WAREHOUSE_TYPES_FIELD,
			_effective_warehouse_types_for_item,
		)

		master = frappe.get_doc("Item", source_row.item_code)
		for warehouse_type in set(_effective_warehouse_types_for_item(master.name, master.item_group)) | {
			"Transit"
		}:
			values = {
				"parent": master.name,
				"parenttype": "Item",
				"parentfield": ITEM_WAREHOUSE_TYPES_FIELD,
				"warehouse_type": warehouse_type,
			}
			if not frappe.db.exists(ALLOWED_WAREHOUSE_TYPE_DOCTYPE, values):
				frappe.get_doc({"doctype": ALLOWED_WAREHOUSE_TYPE_DOCTYPE, **values}).db_insert()
	name = create_vehicle_purchase_receipt(order.name, allocation.name)
	assert name == create_vehicle_purchase_receipt(order.name, allocation.name)
	receipt = frappe.get_doc("Purchase Receipt", name)
	assert receipt.docstatus == 0 and receipt.items[0].qty == 15
	assert receipt.items[0].uom == source_row.uom
	transit = receipt.vuna_pickup_transit_warehouse
	before = (
		frappe.db.get_value("Bin", {"warehouse": transit, "item_code": source_row.item_code}, "actual_qty")
		or 0
	)
	for row in receipt.items:
		master = frappe.get_cached_doc("Item", row.item_code)
		if master.has_serial_no:
			raise AssertionError(
				"Use a nonserialized PO template for this stock smoke; serial boundaries have unit coverage"
			)
		if master.has_batch_no:
			from erpnext.stock.serial_batch_bundle import SerialBatchCreation

			batch = frappe.get_doc(
				{
					"doctype": "Batch",
					"item": row.item_code,
					"batch_id": "SUPP-" + frappe.generate_hash(length=10),
				}
			).insert()
			bundle = SerialBatchCreation(
				frappe._dict(
					item_code=row.item_code,
					company=receipt.company,
					warehouse=transit,
					type_of_transaction="Inward",
					voucher_type="Purchase Receipt",
					voucher_no=receipt.name,
					qty=row.stock_qty,
					do_not_submit=True,
				)
			).make_serial_and_batch_bundle(batch_nos={batch.name: row.stock_qty})
			row.serial_and_batch_bundle = bundle.name
			row.use_serial_batch_fields = 0
			row.batch_no = row.serial_no = None
		receipt.save()
		qi = frappe.get_doc(
			{
				"doctype": "Quality Inspection",
				"inspection_type": "Incoming",
				"reference_type": "Purchase Receipt",
				"reference_name": receipt.name,
				"item_code": row.item_code,
				"sample_size": 1,
				"inspected_by": frappe.session.user,
				"status": "Accepted",
				"manual_inspection": 1,
			}
		).insert()
		qi.submit()
	receipt.reload()
	receipt.submit()
	order.reload()
	assert order.items[0].received_qty == 15
	assert (
		abs(
			(
				frappe.db.get_value(
					"Bin", {"warehouse": transit, "item_code": source_row.item_code}, "actual_qty"
				)
				or 0
			)
			- before
			- receipt.items[0].stock_qty
		)
		< 0.00001
	)
	transfer_name = create_vehicle_material_transfer(receipt.name, source_row.warehouse)
	assert transfer_name == create_vehicle_material_transfer(receipt.name, source_row.warehouse)
	transfer = frappe.get_doc("Stock Entry", transfer_name)
	if partial_transfer:
		for cargo in transfer.items:
			cargo.qty = 7
			if cargo.serial_and_batch_bundle:
				bundle = frappe.get_doc("Serial and Batch Bundle", cargo.serial_and_batch_bundle)
				assert len(bundle.entries) == 1
				bundle.entries[0].qty = -cargo.qty * cargo.conversion_factor
				bundle.save()
		transfer.save()
	assert transfer.docstatus == 0 and transfer.purpose == "Material Transfer"
	assert not transfer.add_to_transit and not transfer.outgoing_stock_entry
	assert all(
		row.s_warehouse == transit and row.t_warehouse == source_row.warehouse for row in transfer.items
	)
	_add_fixture_quality_inspections(transfer)
	transfer.reload()
	transfer.submit()
	transfers = [transfer]
	if partial_transfer:
		remaining_name = create_vehicle_material_transfer(receipt.name, source_row.warehouse)
		assert remaining_name != transfer.name
		remaining = frappe.get_doc("Stock Entry", remaining_name)
		assert all(cargo.qty == 8 for cargo in remaining.items)
		_add_fixture_quality_inspections(remaining)
		remaining.reload()
		remaining.submit()
		transfers.append(remaining)
	assert (
		abs(
			(
				frappe.db.get_value(
					"Bin", {"warehouse": transit, "item_code": source_row.item_code}, "actual_qty"
				)
				or 0
			)
			- before
		)
		< 0.00001
	)
	try:
		create_vehicle_material_transfer(receipt.name, source_row.warehouse)
	except frappe.ValidationError:
		pass
	else:
		raise AssertionError("Transferred receipt quantities must not be received a second time")
	for stock_transfer in reversed(transfers):
		stock_transfer.cancel()
	receipt.reload()
	receipt.cancel()
	order.reload()
	assert order.items[0].received_qty == 0
	return {
		"vehicle_purchase_receipt": "passed",
		"requested_uom": source_row.uom,
		"purchase_receipt_posted_into_vehicle": "passed",
		"normal_material_transfer": "passed",
		"partial_transfer": "passed" if partial_transfer else "not exercised",
		"batch_identity": "passed" if master.has_batch_no else "not applicable",
		"retry_and_overtransfer_guards": "passed",
		"cancellation": "passed",
		"all_changes_rolled_back": True,
	}
