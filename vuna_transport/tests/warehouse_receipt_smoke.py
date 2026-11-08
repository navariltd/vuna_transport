"""Real ERPNext warehouse-only receiving cycle inside task_trip_smoke's rollback."""

import frappe
from frappe.utils import flt


def exercise(trip, task, stock):
	from erpnext.stock.doctype.stock_entry.stock_entry import make_stock_in_entry

	from vuna_transport.services.execution.transfer_proof_service import make_transfer_proof
	from vuna_transport.tests.task_trip_smoke import _add_fixture_quality_inspections

	demand = frappe.get_doc("Transport Demand", task.transport_demand)
	request = frappe.get_doc("Material Request", demand.source_document)
	before_quantities = {row.name: row.ordered_qty for row in request.items}
	assert stock.docstatus == 1
	assert not frappe.db.exists("Proof of Delivery", {"transport_trip_task": task.name})

	def receive(fraction):
		entry = make_stock_in_entry(stock.name)
		for row in entry.items:
			# Take a fraction of each remaining source row using the original UOM.
			row.qty *= fraction
			row.transfer_qty = row.qty * row.conversion_factor
			# Bundles are rebuilt for actual partial quantities, never reused.
			row.serial_and_batch_bundle = None
			row.use_serial_batch_fields = 0
			row.batch_no = row.serial_no = None
		from erpnext.stock.serial_batch_bundle import SerialBatchCreation

		from vuna_transport.services.execution.transfer_proof_service import stock_identities

		for row in entry.items:
			source = next(item for item in stock.items if item.name == row.ste_detail)
			identities = stock_identities(source)
			batches = {
				batch: identity["stock_qty"] * row.transfer_qty / source.transfer_qty
				for batch, identity in identities.items()
				if batch
			}
			if batches:
				# Preserve the source batch proportions for this fixture's partial receipt.
				bundle = SerialBatchCreation(
					frappe._dict(
						item_code=row.item_code,
						company=entry.company,
						warehouse=row.s_warehouse,
						voucher_type="Stock Entry",
						type_of_transaction="Outward",
						qty=-row.transfer_qty,
						do_not_submit=True,
					)
				).make_serial_and_batch_bundle(batch_nos=batches)
				row.serial_and_batch_bundle = bundle.name
			assert (
				row.item_code,
				row.uom,
				row.stock_uom,
				row.conversion_factor,
				row.s_warehouse,
				row.t_warehouse,
			) == (
				source.item_code,
				source.uom,
				source.stock_uom,
				source.conversion_factor,
				stock.to_warehouse,
				demand.destination_warehouse,
			), (row.as_dict(), source.as_dict())
		entry.insert()
		_add_fixture_quality_inspections(entry)
		entry.submit()
		return entry

	partial = receive(0.5)
	task.reload()
	trip.reload()
	assert task.status == "Partially Received", task.status
	assert trip.status != "Completed"
	assert task.confirmation_method == "Warehouse Receipt" and task.receipt_stock_entry == partial.name
	assert task.receipt_confirmed_by == frappe.session.user
	remaining = receive(1)
	task.reload()
	trip.reload()
	assert task.status == "Completed" and trip.status == "Completed"
	remaining.cancel()
	task.reload()
	trip.reload()
	assert task.status == "Partially Received" and trip.status != "Completed"
	try:
		make_transfer_proof(task.name)
	except frappe.ValidationError as error:
		assert "warehouse receipt" in str(error)
	else:
		raise AssertionError("POD would duplicate a posted warehouse receipt")
	partial.cancel()
	task.reload()
	assert task.status == "Ready" and not task.receipt_stock_entry
	full = receive(1)
	task.reload()
	trip.reload()
	assert task.status == "Completed" and trip.status == "Completed"
	assert all(stop.visited for stop in trip.delivery_stops)
	assert task.receipt_stock_entry == full.name
	request.reload()
	assert request.transfer_status == "Completed", request.transfer_status
	assert all(flt(row.ordered_qty) == flt(before_quantities[row.name]) for row in request.items), (
		"Both transport legs were counted against the MR"
	)
	full.cancel()
	task.reload()
	trip.reload()
	assert task.status == "Ready" and trip.status != "Completed"
	assert not task.receipt_stock_entry and not task.confirmation_method
	assert not frappe.db.exists("Proof of Delivery", {"transport_trip_task": task.name})
	return {
		"warehouse_only_partial_full_cancel": "passed",
		"no_duplicate_pod": True,
		"material_request_not_double_counted": True,
		"requested_uom": full.items[0].uom,
		"rolled_back": True,
	}
