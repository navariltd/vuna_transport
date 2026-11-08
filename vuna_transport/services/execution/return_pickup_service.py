"""Depot return collection. Only confirmed pickup quantities enter vehicle stock."""

import math

import frappe
from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
from frappe import _
from frappe.utils import flt, now_datetime


def pickup_context(task):
	if task.get("return_request"):
		from vuna_transport.services.execution.direct_return_pickup_service import direct_context

		return direct_context(task)
	trip = frappe.get_doc("Delivery Trip", task.delivery_trip)
	demand = frappe.get_doc("Transport Demand", task.transport_demand)
	request = frappe.get_doc("Return Request", demand.source_document)
	if (
		task.task_type != "Return Pickup"
		or task.status == "Cancelled"
		or trip.docstatus != 1
		or demand.docstatus != 1
		or demand.source_document_type != "Return Request"
		or request.docstatus != 1
		or request.return_context != "Depot"
		or len({task.company, trip.company, demand.company, request.company}) != 1
	):
		frappe.throw(_("Pickup requires an active depot-return task on a submitted trip."))
	load = frappe.get_doc("Transport Load", task.transport_load)
	if (
		load.docstatus != 1
		or load.company != task.company
		or load.source_warehouse != demand.source_warehouse
	):
		frappe.throw(_("Return pickup must retain its submitted load and source warehouse."))
	return trip, demand, request, load


def planned_pickup_items(task):
	if task.get("return_request"):
		from vuna_transport.services.execution.direct_return_pickup_service import direct_items

		return direct_items(task)
	_trip, demand, request, load = pickup_context(task)
	rows = {row.name: row for row in request.items}
	demand_items = {row.name: row for row in demand.items}
	load_items = {row.name: row for row in load.delivery_orders}
	items = []
	for cargo in task.items:
		planned = load_items.get(cargo.load_item)
		item = demand_items.get(planned.transport_demand_item) if planned else None
		source = rows.get(item.source_item) if item else None
		if (
			not source
			or source.custody_warehouse != demand.source_warehouse
			or source.item_code != cargo.item_code
		):
			frappe.throw(_("Pickup cargo must reference the return items held at this depot."))
		items.append(
			{
				"source_row": source.name,
				"item_code": source.item_code,
				"item_name": source.item_name,
				"ordered_qty": cargo.qty,
				"delivered_qty": cargo.qty,
				"rejected_qty": 0,
				"uom": cargo.uom,
				"stock_uom": cargo.stock_uom,
				"conversion_factor": cargo.conversion_factor,
				"stock_qty": cargo.stock_qty,
				"batch_no": source.batch_no,
				"serial_no": source.serial_no or "",
				"source_bundle": source.source_bundle,
			}
		)
	return items


def prepare_pickup_proof(task):
	trip, _demand, request, _load = pickup_context(task)
	existing = frappe.db.get_value(
		"Proof of Delivery", {"transport_trip_task": task.name, "docstatus": ["!=", 2]}, "name"
	)
	if existing:
		return frappe.get_doc("Proof of Delivery", existing)
	if trip.vuna_actual_completion:
		frappe.throw(_("This trip is already completed."))
	proof = frappe.new_doc("Proof of Delivery")
	proof.update(
		{
			"proof_type": "Return Pickup",
			"direction": "Pickup",
			"transport_trip_task": task.name,
			"company": task.company,
			"delivery_trip": trip.name,
			"delivery_stop": task.delivery_stop,
			"delivery_load": task.transport_load,
			"delivery_address": task.address,
			"return_request": request.name,
			"customer": getattr(request, "customer", None),
		}
	)
	proof.set("items", planned_pickup_items(task))
	return proof


def validate_pickup_proof(proof):
	from vuna_transport.services.demand.depot_return_demand_service import posted_pickup_quantities
	from vuna_transport.services.execution.transfer_execution_service import _lock_task
	from vuna_transport.services.shared.security_service import require_document_permission

	task = _lock_task(proof.transport_trip_task)
	if not proof.flags.ignore_permissions:
		require_document_permission(task)
	trip, _demand, request, _load = pickup_context(task)
	frappe.db.get_value("Return Request", request.name, "name", for_update=True)
	if task.get("return_request"):
		from vuna_transport.services.execution.direct_return_pickup_service import _approval

		request.reload()
		_approval(request)
	if not trip.vuna_actual_start or trip.vuna_actual_completion:
		frappe.throw(_("Start the assigned trip before recording return pickup."))
	if frappe.db.exists(
		"Proof of Delivery",
		{"transport_trip_task": task.name, "docstatus": ["!=", 2], "name": ["!=", proof.name or ""]},
	):
		frappe.throw(_("This pickup task already has an active POD."))
	previous = proof.get_doc_before_save()
	if previous and previous.transport_trip_task != task.name:
		frappe.throw(_("The pickup task link cannot be changed."))
	expected = {row["source_row"]: row for row in planned_pickup_items(task)}
	posted = posted_pickup_quantities(list(expected))
	if task.get("return_request") and request.return_context != "Depot":
		from vuna_transport.services.execution.direct_return_pickup_service import collected_quantities

		posted = collected_quantities(request)
	source_rows = {row.name: row for row in request.items}
	precision = frappe.get_precision("Stock Entry Detail", "transfer_qty")
	seen, serials = set(), set()
	for row in proof.items:
		planned = expected.get(row.source_row)
		if not planned or row.source_row in seen:
			frappe.throw(_("Keep each planned return item exactly once in pickup POD."))
		qty = flt(row.delivered_qty)
		stock_qty = flt(qty * flt(planned["conversion_factor"]), precision)
		for uom, amount in ((planned["uom"], qty), (planned["stock_uom"], stock_qty)):
			if frappe.db.get_value("UOM", uom, "must_be_whole_number") and abs(amount - round(amount)) > 1e-9:
				frappe.throw(_("{0} requires whole-number pickup quantities.").format(uom))
		available = flt(source_rows[row.source_row].requested_return_qty) * flt(
			planned["conversion_factor"]
		) - flt(posted.get(row.source_row))
		if task.get("return_request"):
			available = min(
				available,
				(
					flt(source_rows[row.source_row].requested_return_qty)
					- flt(source_rows[row.source_row].received_qty)
				)
				* flt(planned["conversion_factor"]),
			)
		if (
			not math.isfinite(qty)
			or qty < 0
			or flt(stock_qty - min(flt(planned["stock_qty"]), available), precision) > 0
			or flt(row.rejected_qty)
		):
			frappe.throw(
				_(
					"Pickup quantity cannot exceed its planned or outstanding return quantity. Enter only goods actually collected."
				)
			)
		chosen = get_serial_nos(row.serial_no or "")
		allowed = set(get_serial_nos(planned["serial_no"]))
		if (
			len(chosen) != len(set(chosen))
			or serials.intersection(chosen)
			or not set(chosen).issubset(allowed)
			or (allowed and len(chosen) != stock_qty)
		):
			frappe.throw(_("Choose the return serial numbers matching the collected quantity."))
		serials.update(chosen)
		for field in (
			"item_code",
			"item_name",
			"ordered_qty",
			"uom",
			"stock_uom",
			"conversion_factor",
			"batch_no",
			"source_bundle",
		):
			row.set(field, planned[field])
		row.stock_qty, row.rejected_qty = stock_qty, 0
		row.missing_qty = max(flt(planned["ordered_qty"]) - qty, 0)
		row.depot_received_rejected_qty = row.rejected_with_driver_qty = 0
		row.rejected_goods_handling = None
		seen.add(row.source_row)
	if seen != set(expected):
		frappe.throw(_("Keep all planned return rows, including zero collected quantities."))
	proof.company, proof.delivery_trip, proof.delivery_stop = task.company, trip.name, task.delivery_stop
	proof.proof_type, proof.direction = "Return Pickup", "Pickup"
	proof.return_request, proof.delivery_load, proof.delivery_address = (
		request.name,
		task.transport_load,
		task.address,
	)
	proof.customer = getattr(request, "customer", None)
	proof.total_accepted_qty = sum(flt(row.delivered_qty) for row in proof.items)
	proof.total_rejected_qty = 0
	proof.delivery_datetime = proof.delivery_datetime or now_datetime()
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
	if proof.docstatus == 1:
		if not (proof.received_by or "").strip():
			frappe.throw(_("Enter the person confirming release of the returned goods."))
		if proof.outcome != "Completed" and not (proof.remarks or "").strip():
			frappe.throw(_("Explain the uncollected quantity in Remarks."))
		proof.recorded_by, proof.delivery_datetime = frappe.session.user, now_datetime()


def post_pickup_stock(proof, prepare_only=False):
	from vuna_transport.services.execution.allocation_transfer_service import (
		resolve_vehicle_transit_warehouse,
		set_allocation_stock_defaults,
	)

	if not any(flt(row.delivered_qty) > 0 for row in proof.items):
		return
	task = frappe.get_doc("Transport Trip Task", proof.transport_trip_task)
	trip, demand, _request, load = pickup_context(task)
	if task.get("return_request") and _request.return_context != "Depot":
		return  # Customer goods enter inventory through the reviewed Sales Return, not a fake warehouse transfer.
	source_warehouse = _request.source_warehouse if task.get("return_request") else demand.source_warehouse
	if task.get("return_request"):
		from vuna_transport.services.execution.direct_return_pickup_service import _source_warehouse

		source_warehouse = _source_warehouse(_request)
	existing = frappe.db.get_value(
		"Stock Entry", {"vuna_return_pickup_proof": proof.name, "docstatus": ["!=", 2]}, "name"
	)
	if existing:
		entry = frappe.get_doc("Stock Entry", existing)
		if prepare_only:
			return entry.name
		if entry.docstatus == 0:
			entry.flags.ignore_permissions = True
			entry.submit()
		proof.db_set("dispatch_stock_entry", entry.name, update_modified=False)
		if task.get("return_request"):
			task.db_set("dispatch_stock_entry", entry.name, update_modified=False)
		else:
			frappe.db.set_value(
				"Transport Trip Task",
				{
					"delivery_trip": task.delivery_trip,
					"transport_load": task.transport_load,
					"transport_demand": task.transport_demand,
				},
				"dispatch_stock_entry",
				entry.name,
				update_modified=False,
			)
		return entry.name
	allocation = (
		trip
		if task.get("return_request")
		else frappe.get_doc(
			"Vehicle Allocation", trip.vuna_current_vehicle_allocation or trip.vuna_vehicle_allocation
		)
	)
	transit = resolve_vehicle_transit_warehouse(allocation, check_permissions=False)
	entry = frappe.new_doc("Stock Entry")
	entry.update(
		{
			"company": proof.company,
			"purpose": "Material Transfer",
			"stock_entry_type": "Material Transfer",
			"add_to_transit": 1,
			"from_warehouse": source_warehouse,
			"to_warehouse": transit,
			"vuna_return_pickup_proof": proof.name,
		}
	)
	sources = {row.name: row for row in demand.items} if demand else {}
	cargo = {
		sources[row.transport_demand_item].source_item: row
		for row in (load.delivery_orders if load else [])
		if row.transport_demand == demand.name
	}
	for result in proof.items:
		if not flt(result.delivered_qty):
			continue
		row = entry.append(
			"items",
			{
				"item_code": result.item_code,
				"qty": result.delivered_qty,
				"uom": result.uom,
				"stock_uom": result.stock_uom,
				"conversion_factor": result.conversion_factor,
				"s_warehouse": source_warehouse,
				"t_warehouse": transit,
				"vuna_transport_load_item": cargo[result.source_row].name if load else None,
				"vuna_return_request_item": result.source_row,
			},
		)
		if result.batch_no or result.serial_no:
			chosen = get_serial_nos(result.serial_no or "")
			bundle = frappe.get_doc(
				{
					"doctype": "Serial and Batch Bundle",
					"item_code": result.item_code,
					"company": proof.company,
					"warehouse": source_warehouse,
					"type_of_transaction": "Outward",
					"voucher_type": "Stock Entry",
					"entries": [
						{"serial_no": serial, "batch_no": result.batch_no, "qty": -1} for serial in chosen
					]
					if chosen
					else [{"batch_no": result.batch_no, "qty": -flt(result.stock_qty)}],
				}
			).insert(ignore_permissions=True)
			row.serial_and_batch_bundle = bundle.name
	entry.flags.ignore_permissions = True
	set_allocation_stock_defaults(entry, allocation)
	entry.insert(ignore_permissions=True)
	if prepare_only:
		return entry.name
	entry.submit()
	proof.db_set("dispatch_stock_entry", entry.name, update_modified=False)
	if task.get("return_request"):
		task.db_set("dispatch_stock_entry", entry.name, update_modified=False)
		return
	frappe.db.set_value(
		"Transport Trip Task",
		{
			"delivery_trip": task.delivery_trip,
			"transport_load": task.transport_load,
			"transport_demand": task.transport_demand,
		},
		"dispatch_stock_entry",
		entry.name,
		update_modified=False,
	)


def validate_pickup_stock(entry, method=None):
	previous = entry.get_doc_before_save()
	if previous and previous.get("vuna_return_pickup_proof") != entry.get("vuna_return_pickup_proof"):
		frappe.throw(_("The return pickup POD link cannot be changed on a saved Stock Entry."))
	if not entry.get("vuna_return_pickup_proof"):
		from vuna_transport.services.execution.direct_return_pickup_service import validate_direct_receipt

		validate_direct_receipt(entry)
		return
	from vuna_transport.services.execution.transfer_execution_service import _lock_task
	from vuna_transport.services.execution.transfer_proof_service import stock_identities

	proof = frappe.get_doc("Proof of Delivery", entry.vuna_return_pickup_proof)
	if not entry.flags.ignore_permissions:
		from vuna_transport.services.shared.security_service import require_document_permission

		require_document_permission(proof)
	task = _lock_task(proof.transport_trip_task)
	trip, demand, _request, load = pickup_context(task)
	if task.get("return_request") and _request.return_context != "Depot":
		frappe.throw(_("Customer pickup tasks do not authorize warehouse stock transfers."))
	source_warehouse = demand.source_warehouse if demand else None
	if task.get("return_request"):
		from vuna_transport.services.execution.direct_return_pickup_service import _source_warehouse

		source_warehouse = _source_warehouse(_request)
	allocation = (
		trip
		if task.get("return_request")
		else frappe.get_doc(
			"Vehicle Allocation", trip.vuna_current_vehicle_allocation or trip.vuna_vehicle_allocation
		)
	)
	from vuna_transport.services.execution.allocation_transfer_service import (
		resolve_vehicle_transit_warehouse,
	)

	transit = resolve_vehicle_transit_warehouse(allocation, check_permissions=False)
	if (
		(proof.docstatus != 1 and not (proof.docstatus == 0 and entry.docstatus == 0))
		or proof.proof_type != "Return Pickup"
		or entry.company != proof.company
		or not entry.add_to_transit
		or entry.purpose != "Material Transfer"
		or entry.stock_entry_type != "Material Transfer"
		or entry.outgoing_stock_entry
		or entry.get("vuna_vehicle_allocation")
		or entry.get("vuna_transport_load")
		or entry.get("vuna_transport_proof")
		or entry.get("vuna_transport_task")
		or entry.from_warehouse != source_warehouse
		or entry.to_warehouse != transit
	):
		frappe.throw(_("Return pickup stock must match its submitted pickup POD and assigned vehicle."))
	if frappe.db.exists(
		"Stock Entry",
		{"vuna_return_pickup_proof": proof.name, "docstatus": ["!=", 2], "name": ["!=", entry.name or ""]},
	):
		frappe.throw(_("This return pickup already has an active stock transaction."))
	expected = {row.source_row: row for row in proof.items if flt(row.delivered_qty) > 0}
	from vuna_transport.services.demand.depot_return_demand_service import posted_pickup_quantities

	frappe.db.get_value("Return Request", _request.name, "name", for_update=True)
	posted = posted_pickup_quantities(list(expected), exclude_entry=entry.name)
	request_rows = {row.name: row for row in _request.items}
	seen = set()
	for row in entry.items:
		result = expected.get(row.vuna_return_request_item)
		if not result or row.vuna_return_request_item in seen:
			frappe.throw(_("Return pickup Stock Entry must contain each collected item exactly once."))
		available = flt(request_rows[result.source_row].requested_return_qty) * flt(
			result.conversion_factor
		) - flt(posted.get(result.source_row))
		if flt(flt(row.transfer_qty) - available, row.precision("transfer_qty")) > 0:
			frappe.throw(_("This pickup exceeds the remaining Return Request quantity."))
		load_row = next(
			(
				cargo
				for cargo in (load.delivery_orders if load else [])
				if cargo.name == row.vuna_transport_load_item
			),
			None,
		)
		if (
			(
				load
				and (
					not load_row
					or load_row.transport_demand != demand.name
					or frappe.db.get_value(
						"Transport Demand Item", load_row.transport_demand_item, "source_item"
					)
					!= result.source_row
				)
			)
			or (
				not load
				and (
					row.vuna_transport_load_item
					or result.source_row not in {item.return_request_item for item in task.items}
				)
			)
			or row.item_code != result.item_code
			or row.uom != result.uom
			or row.stock_uom != result.stock_uom
			or flt(row.conversion_factor) != flt(result.conversion_factor)
			or row.s_warehouse != source_warehouse
			or row.t_warehouse != transit
			or flt(flt(row.transfer_qty) - flt(result.stock_qty), row.precision("transfer_qty"))
		):
			frappe.throw(
				_("Pickup stock must retain its exact return quantity, source, destination and UOM.")
			)
		identity = stock_identities(row)
		if set(identity) != {result.batch_no or ""} or set(identity[result.batch_no or ""]["serials"]) != set(
			get_serial_nos(result.serial_no or "")
		):
			frappe.throw(_("Pickup stock batch/serial identity must match its POD."))
		if flt(
			identity[result.batch_no or ""]["stock_qty"] - flt(result.stock_qty),
			row.precision("transfer_qty"),
		):
			frappe.throw(_("Pickup stock bundle quantity must match its POD."))
		seen.add(result.source_row)
	if seen != set(expected):
		frappe.throw(_("Keep every collected return item in pickup stock."))


def pickup_stock_for_task(task):
	pickup = (
		task.name
		if task.get("return_request")
		else frappe.db.get_value(
			"Transport Trip Task",
			{
				"delivery_trip": task.delivery_trip,
				"transport_demand": task.transport_demand,
				"transport_load": task.transport_load,
				"task_type": "Return Pickup",
			},
			"name",
		)
	)
	proof = (
		frappe.db.get_value("Proof of Delivery", {"transport_trip_task": pickup, "docstatus": 1}, "name")
		if pickup
		else None
	)
	return (
		frappe.db.get_value("Stock Entry", {"vuna_return_pickup_proof": proof, "docstatus": 1}, "name")
		if proof
		else None
	)


def pickup_status(task):
	proof = frappe.db.get_value(
		"Proof of Delivery",
		{"transport_trip_task": task.name, "docstatus": 1},
		["name", "outcome"],
		as_dict=True,
	)
	if not proof:
		return "Ready"
	if proof.outcome == "Failed":
		return "Failed"
	if (
		task.get("return_request")
		and frappe.db.get_value("Return Request", task.return_request, "return_context") != "Depot"
	):
		return "Completed" if proof.outcome == "Completed" else "Completed with Variance"
	if not pickup_stock_for_task(task):
		return "Awaiting Stock Posting"
	return "Completed" if proof.outcome == "Completed" else "Completed with Variance"


def sync_pickup_stock(entry, method=None):
	proof_name = entry.get("vuna_return_pickup_proof") or (
		frappe.db.get_value("Stock Entry", entry.outgoing_stock_entry, "vuna_return_pickup_proof")
		if entry.get("outgoing_stock_entry")
		else None
	)
	if not proof_name:
		return
	from vuna_transport.services.execution.trip_task_service import sync_task_execution

	proof = frappe.get_doc("Proof of Delivery", proof_name)
	sync_task_execution(proof)
	from vuna_transport.services.execution.return_service import recalculate_return_request
	from vuna_transport.services.planning.demand_lifecycle_service import sync_demand_allocations

	task = frappe.get_doc("Transport Trip Task", proof.transport_trip_task)
	sync_demand_allocations([task.transport_demand])
	recalculate_return_request(proof.return_request)


def sync_pickup_proof(proof):
	from vuna_transport.services.planning.demand_lifecycle_service import sync_demand_allocations

	task = frappe.get_doc("Transport Trip Task", proof.transport_trip_task)
	sync_demand_allocations([task.transport_demand])


def failed_pickup(task):
	pickup = frappe.db.get_value(
		"Transport Trip Task",
		{
			"delivery_trip": task.delivery_trip,
			"transport_demand": task.transport_demand,
			"transport_load": task.transport_load,
			"task_type": "Return Pickup",
		},
		"name",
	)
	return bool(
		pickup
		and frappe.db.exists(
			"Proof of Delivery", {"transport_trip_task": pickup, "docstatus": 1, "outcome": "Failed"}
		)
	)


def guard_pickup_proof_cancellation(proof):
	if frappe.db.exists("Stock Entry", {"vuna_return_pickup_proof": proof.name, "docstatus": ["!=", 2]}):
		frappe.throw(_("Cancel or delete the pickup Stock Entry before cancelling its POD."))


def guard_pickup_stock_cancellation(entry, method=None):
	if not entry.get("vuna_return_pickup_proof"):
		return
	if frappe.db.exists("Stock Entry", {"outgoing_stock_entry": entry.name, "docstatus": ["!=", 2]}):
		frappe.throw(_("Cancel/delete the End Transit receipt before cancelling pickup stock."))
	if frappe.db.exists(
		"Proof of Delivery",
		{
			"dispatch_stock_entry": entry.name,
			"name": ["!=", entry.vuna_return_pickup_proof],
			"docstatus": ["!=", 2],
		},
	):
		frappe.throw(_("Cancel/delete the receiving POD before cancelling pickup stock."))
	# These are navigation backlinks, not a second stock authority. Keep the
	# original pickup proof until its stock has been cancelled, then cancel it.
	entry.ignore_linked_doctypes = list(
		set(getattr(entry, "ignore_linked_doctypes", []) or []) | {"Proof of Delivery", "Transport Trip Task"}
	)
