"""Supplier handover evidence and optional, task-scoped vehicle receipts."""

import math

import frappe
from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
from frappe import _
from frappe.utils import flt, now_datetime


def collection_context(task):
	from vuna_transport.services.execution.supplier_vehicle_receipt_service import receipt_context

	trip = frappe.get_doc("Delivery Trip", task.delivery_trip)
	demand = frappe.get_doc("Transport Demand", task.transport_demand)
	if (
		task.task_type != "Supplier Pickup"
		or task.status == "Cancelled"
		or trip.docstatus != 1
		or trip.vuna_actual_completion
		or demand.docstatus != 1
		or demand.source_document_type != "Purchase Order"
		or len({task.company, trip.company, demand.company}) != 1
		or not any(row.name == task.delivery_stop for row in trip.delivery_stops)
	):
		frappe.throw(_("Collection requires an active supplier pickup task on its submitted trip."))
	order, allocation, transit, legs = receipt_context(
		demand.source_document,
		trip.vuna_current_vehicle_allocation or trip.vuna_vehicle_allocation,
		check_permissions=False,
	)
	if allocation.vehicle != (trip.get("vuna_current_vehicle") or trip.vehicle):
		frappe.throw(_("The collection vehicle must match the trip's current allocation."))
	if task.get("transport_load"):
		load = frappe.get_doc("Transport Load", task.transport_load)
		allowed = {row.name: row for row in load.delivery_orders}
		if (
			load.docstatus != 1
			or load.active_vehicle_allocation != allocation.name
			or load.delivery_trip != trip.name
			or load.source_address != demand.source_address
		):
			frappe.throw(_("The supplier pickup must retain its submitted load and allocation."))
		for cargo in task.items:
			row = allowed.get(cargo.load_item)
			if (
				not row
				or row.transport_demand != demand.name
				or row.transport_demand_item != cargo.transport_demand_item
				or flt(cargo.qty) != flt(row.allocated_quantity)
			):
				frappe.throw(_("Supplier pickup quantities must match their allocated load rows."))
	return trip, demand, order, allocation, transit, legs


def planned_collection_items(task, demand, legs):
	sources = {row.name: row for row in demand.items}
	available = {item["source_item"]: item for leg in legs for item in leg["items"]}
	items, seen = [], set()
	for cargo in task.items:
		source = sources.get(cargo.transport_demand_item)
		item = available.get(source.source_item) if source else None
		if (
			not item
			or source.source_item in seen
			or cargo.item_code != item["item_code"]
			or cargo.uom != item["transaction_uom"]
			or cargo.stock_uom != item["stock_uom"]
			or flt(cargo.conversion_factor) != item["conversion_factor"]
			or not math.isfinite(flt(cargo.qty))
			or flt(cargo.qty) <= 0
		):
			frappe.throw(_("Supplier pickup items must retain their Purchase Order and UOM references."))
		seen.add(source.source_item)
		items.append(
			{
				"source_row": source.source_item,
				"item_code": cargo.item_code,
				"item_name": frappe.get_cached_value("Item", cargo.item_code, "item_name"),
				"ordered_qty": cargo.qty,
				"delivered_qty": min(flt(cargo.qty), item["transaction_qty"]),
				"uom": cargo.uom,
				"stock_uom": cargo.stock_uom,
				"conversion_factor": cargo.conversion_factor,
			}
		)
	if not items:
		frappe.throw(_("This supplier pickup task has no outstanding stock items."))
	return items


def prepare_collection(task):
	if existing := frappe.db.get_value(
		"Proof of Delivery", {"transport_trip_task": task.name, "docstatus": ["!=", 2]}, "name"
	):
		return frappe.get_doc("Proof of Delivery", existing)
	trip, demand, _order, _allocation, _transit, legs = collection_context(task)
	assert_no_receipt(task)
	proof = frappe.new_doc("Proof of Delivery")
	proof.update(
		{
			"proof_type": "Supplier Pickup",
			"direction": "Pickup",
			"transport_trip_task": task.name,
			"transport_demand": demand.name,
			"company": task.company,
			"delivery_trip": trip.name,
			"delivery_stop": task.delivery_stop,
			"delivery_load": task.transport_load,
			"delivery_address": task.address,
		}
	)
	proof.set("items", planned_collection_items(task, demand, legs))
	return proof


def assert_no_receipt(task):
	from vuna_transport.services.execution.supplier_receipt_confirmation_service import task_progress

	if task_progress(task)["evidence"]:
		frappe.throw(
			_(
				"This pickup already has a Purchase Receipt. Receive remaining goods through purchasing, not a second collection proof."
			)
		)


def validate_collection(proof):
	from vuna_transport.services.execution.transfer_execution_service import _lock_task
	from vuna_transport.services.shared.security_service import require_document_permission

	task = _lock_task(proof.transport_trip_task)
	if not proof.flags.ignore_permissions:
		require_document_permission(task)
	trip, demand, _order, _allocation, _transit, legs = collection_context(task)
	assert_no_receipt(task)
	previous = proof.get_doc_before_save()
	if previous and previous.transport_trip_task != task.name:
		frappe.throw(_("The collection task cannot be changed."))
	if not trip.vuna_actual_start:
		frappe.throw(_("Start the trip before recording supplier collection."))
	if frappe.db.exists(
		"Proof of Delivery",
		{"transport_trip_task": task.name, "docstatus": ["!=", 2], "name": ["!=", proof.name or ""]},
	):
		frappe.throw(_("This supplier pickup already has an active proof."))
	expected = {row["source_row"]: row for row in planned_collection_items(task, demand, legs)}
	precision = frappe.get_precision("Purchase Receipt Item", "stock_qty")
	seen, serials = set(), set()
	for row in proof.items:
		planned = expected.get(row.source_row)
		qty = flt(row.delivered_qty)
		if (
			not planned
			or row.source_row in seen
			or not math.isfinite(qty)
			or qty < 0
			or flt((qty - flt(planned["delivered_qty"])) * flt(planned["conversion_factor"]), precision) > 0
			or flt(row.rejected_qty)
		):
			frappe.throw(
				_(
					"Collection quantities must be finite and cannot exceed the assigned, unreceived PO quantity."
				)
			)
		for field in ("item_code", "item_name", "ordered_qty", "uom", "stock_uom", "conversion_factor"):
			row.set(field, planned[field])
		row.stock_qty = flt(qty * flt(row.conversion_factor), precision)
		if qty > 0 and row.stock_qty <= 0:
			frappe.throw(_("The collected quantity is below the configured stock precision."))
		for uom, amount in ((row.uom, qty), (row.stock_uom, row.stock_qty)):
			if frappe.db.get_value("UOM", uom, "must_be_whole_number") and flt(
				amount - round(amount), precision
			):
				frappe.throw(_("{0} requires whole-number collection quantities.").format(uom))
		chosen = get_serial_nos(row.serial_no or "")
		has_batch, has_serial = frappe.get_cached_value(
			"Item", row.item_code, ["has_batch_no", "has_serial_no"]
		)
		row.batch_no = (row.batch_no or "").strip() or None
		if row.batch_no and (
			not has_batch or frappe.db.get_value("Batch", row.batch_no, "item") not in (None, row.item_code)
		):
			frappe.throw(_("The collected batch must belong to this batched item."))
		if chosen and (not has_serial or len(chosen) != row.stock_qty):
			frappe.throw(_("Collected serial numbers must match the item's stock quantity."))
		if len(chosen) != len(set(chosen)) or serials.intersection(chosen):
			frappe.throw(_("Collected serial numbers must not be repeated."))
		serials.update(chosen)
		row.missing_qty = max(flt(row.ordered_qty) - qty, 0)
		row.rejected_qty = row.rejected_stock_qty = row.depot_received_rejected_qty = (
			row.rejected_with_driver_qty
		) = 0
		row.source_bundle = row.rejected_serial_no = row.depot_received_rejected_serial_no = (
			row.rejected_goods_handling
		) = None
		seen.add(row.source_row)
	if seen != set(expected):
		frappe.throw(_("Keep every assigned Purchase Order row, including zero collected quantities."))
	proof.proof_type, proof.direction = "Supplier Pickup", "Pickup"
	proof.customer = proof.delivery_note = proof.return_request = proof.dispatch_stock_entry = None
	proof.company, proof.delivery_trip, proof.delivery_stop = task.company, trip.name, task.delivery_stop
	proof.transport_demand, proof.delivery_load, proof.delivery_address = (
		demand.name,
		task.transport_load,
		task.address,
	)
	proof.total_accepted_qty = sum(flt(row.delivered_qty) for row in proof.items)
	proof.total_rejected_qty = 0
	proof.outcome = (
		"Completed"
		if all(not flt(row.missing_qty, precision) for row in proof.items)
		else "Partially Completed"
		if proof.total_accepted_qty
		else "Failed"
	)
	proof.status = (
		"Accepted"
		if proof.outcome == "Completed"
		else "Partially Delivered"
		if proof.total_accepted_qty
		else "Failed"
	)
	proof.delivery_datetime = proof.delivery_datetime or now_datetime()
	if proof.docstatus == 1:
		if not (proof.received_by or "").strip():
			frappe.throw(_("Enter the name of the person releasing the goods."))
		if proof.outcome != "Completed" and not (proof.remarks or "").strip():
			frappe.throw(_("Explain uncollected goods in Remarks."))
		proof.recorded_by, proof.delivery_datetime = frappe.session.user, now_datetime()


def post_collection_receipt(proof):
	from erpnext.buying.doctype.purchase_order.purchase_order import set_missing_values
	from frappe.model.mapper import get_mapped_doc

	from vuna_transport.services.execution.allocation_transfer_service import set_allocation_stock_defaults
	from vuna_transport.services.execution.driver_stock_policy import driver_purchase_receipts_enabled
	from vuna_transport.services.execution.transfer_execution_service import _lock_task

	if (
		proof.docstatus != 1
		or proof.proof_type != "Supplier Pickup"
		or not driver_purchase_receipts_enabled()
		or not any(flt(row.delivered_qty) > 0 for row in proof.items)
	):
		return
	task = _lock_task(proof.transport_trip_task)
	_trip, _demand, order, allocation, transit, _legs = collection_context(task)
	assert_no_receipt(task)
	if frappe.db.exists("Purchase Receipt", {"vuna_pickup_task": task.name, "docstatus": ["!=", 2]}):
		frappe.throw(_("Review the existing pickup Purchase Receipt before posting collection proof."))
	actual = {row.source_row: row for row in proof.items if flt(row.delivered_qty) > 0}
	# Use Frappe's standard mapper and ERPNext's purchasing defaults. Only this
	# validated task's rows may cross the driver's stock-permission boundary.
	receipt = get_mapped_doc(
		"Purchase Order",
		order.name,
		{
			"Purchase Order": {"doctype": "Purchase Receipt", "validation": {"docstatus": ["=", 1]}},
			"Purchase Order Item": {
				"doctype": "Purchase Receipt Item",
				"field_map": {
					"name": "purchase_order_item",
					"parent": "purchase_order",
					"material_request": "material_request",
					"material_request_item": "material_request_item",
				},
				"condition": lambda row: row.name in actual,
			},
			"Purchase Taxes and Charges": {"doctype": "Purchase Taxes and Charges", "reset_value": True},
		},
		ignore_permissions=True,
	)
	receipt.vuna_pickup_purchase_order, receipt.vuna_pickup_allocation = order.name, allocation.name
	receipt.vuna_pickup_task, receipt.vuna_pickup_transit_warehouse = task.name, transit
	receipt.set_warehouse, receipt.apply_putaway_rule = transit, 0
	receipt.rejected_warehouse = None
	for row in receipt.items:
		result = actual[row.purchase_order_item]
		row.vuna_pickup_destination_warehouse = row.warehouse
		row.warehouse, row.rejected_warehouse = transit, None
		row.qty = row.received_qty = result.delivered_qty
		row.stock_qty = row.received_stock_qty = result.stock_qty
		row.rejected_qty = 0
		row.serial_and_batch_bundle = None
		row.use_serial_batch_fields = 1
		row.batch_no, row.serial_no = result.batch_no, result.serial_no
	set_missing_values(order, receipt)
	set_allocation_stock_defaults(receipt, allocation)
	receipt.flags.ignore_permissions = True
	# ERPNext still validates outstanding PO quantity, UOM, batches, serials,
	# mandatory inspections, accounting and stock ledgers. Failure rolls back POD.
	receipt.insert(ignore_permissions=True)
	receipt.submit()
	return receipt.name


def collection_status(task, progress):
	name = frappe.db.get_value(
		"Proof of Delivery",
		{"transport_trip_task": task.name, "proof_type": "Supplier Pickup", "docstatus": 1},
		"name",
	)
	if not name:
		return None
	proof = frappe.get_doc("Proof of Delivery", name)
	if proof.outcome == "Failed":
		return "Failed"
	precision = frappe.get_precision("Purchase Receipt Item", "stock_qty")
	if any(
		flt(flt(row.stock_qty) - flt(progress["received"].get(row.source_row)), precision) > 0
		for row in proof.items
	):
		return "Awaiting Stock Posting"
	return "Completed" if proof.outcome == "Completed" else "Completed with Variance"


def guard_collection_cancellation(proof):
	if frappe.db.exists(
		"Purchase Receipt", {"vuna_pickup_task": proof.transport_trip_task, "docstatus": ["!=", 2]}
	):
		frappe.throw(_("Cancel or delete the collection Purchase Receipt before cancelling its proof."))
