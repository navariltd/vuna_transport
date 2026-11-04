"""Warehouse-transfer dispatch preparation; ERPNext owns stock and accounting."""

import math
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

from vuna_transport.services.shared.security_service import (
	require_doctype_permission,
	require_document_permission,
)


def _lock_task(name):
	trip_name = frappe.db.get_value("Transport Trip Task", name, "delivery_trip")
	if trip_name:
		frappe.db.get_value("Delivery Trip", trip_name, "name", for_update=True)
	frappe.db.get_value("Transport Trip Task", name, "name", for_update=True)
	return frappe.get_doc("Transport Trip Task", name)


def _dispatch_context(task):
	trip = frappe.get_doc("Delivery Trip", task.delivery_trip)
	demand = frappe.get_doc("Transport Demand", task.transport_demand)
	load = frappe.get_doc("Transport Load", task.transport_load)
	if (
		task.task_type != "Transfer Dispatch"
		or task.status == "Cancelled"
		or trip.docstatus != 1
		or not trip.vuna_task_based
		or demand.docstatus != 1
		or load.docstatus != 1
		or demand.movement_type != "Warehouse Transfer"
		or demand.source_document_type != "Material Request"
		or len({task.company, trip.company, demand.company, load.company}) != 1
	):
		frappe.throw(_("Dispatch requires an active transfer task on a submitted trip."))
	return trip, demand, load


def get_dispatch_entry(task_name):
	legacy = frappe.db.get_value(
		"Stock Entry", {"vuna_transport_task": task_name, "docstatus": ["!=", 2]}, "name"
	)
	if legacy:
		return legacy
	task = frappe.get_doc("Transport Trip Task", task_name)
	trip = frappe.get_doc("Delivery Trip", task.delivery_trip)
	from vuna_transport.services.execution.allocation_transfer_service import allocation_stock_entry

	entry = allocation_stock_entry(
		trip.vuna_current_vehicle_allocation or trip.vuna_vehicle_allocation, task.transport_load
	)
	if entry:
		return entry
	# Compatibility for stock prepared under the original dispatch-task flow.
	if task.task_type == "Transfer Receipt":
		dispatch = frappe.db.get_value(
			"Transport Trip Task",
			{
				"delivery_trip": task.delivery_trip,
				"transport_load": task.transport_load,
				"transport_demand": task.transport_demand,
				"task_type": "Transfer Dispatch",
			},
			"name",
		)
		if dispatch:
			return frappe.db.get_value(
				"Stock Entry", {"vuna_transport_task": dispatch, "docstatus": ["!=", 2]}, "name"
			)


@frappe.whitelist()
def make_dispatch_stock_entry(source_name: str, transit_warehouse: str):
	"""Prepare an unsaved Add-to-Transit entry for authorized warehouse review."""
	require_doctype_permission("Stock Entry", "create")
	task = _lock_task(source_name)
	require_document_permission(task)
	trip, demand, load = _dispatch_context(task)
	for doc in (trip, demand, load):
		require_document_permission(doc)
	if existing := get_dispatch_entry(task.name):
		entry = frappe.get_doc("Stock Entry", existing)
		require_document_permission(entry)
		return entry
	if trip.vuna_actual_start:
		frappe.throw(_("Prepare dispatch stock documents before starting the trip."))
	_validate_transit_warehouse(transit_warehouse, task.company, demand.source_warehouse)
	# The standard mapper retains MR lineage, accounting defaults and item metadata.
	from erpnext.stock.doctype.material_request.material_request import make_stock_entry

	entry = make_stock_entry(demand.source_document)
	by_source = {row.material_request_item: row for row in entry.items}
	demand_items = {row.name: row for row in demand.items}
	load_items = {row.name: row for row in load.delivery_orders}
	selected = []
	for cargo in task.items:
		planned = load_items.get(cargo.load_item)
		source = demand_items.get(planned.transport_demand_item) if planned else None
		row = by_source.get(source.source_item) if source else None
		if not row or flt(row.transfer_qty) < flt(cargo.stock_qty):
			frappe.throw(_("The Material Request no longer has the planned quantity available."))
		row.qty = cargo.qty
		row.uom = cargo.uom
		row.stock_uom = cargo.stock_uom
		row.conversion_factor = cargo.conversion_factor
		row.transfer_qty = cargo.stock_qty
		row.s_warehouse = demand.source_warehouse
		row.t_warehouse = transit_warehouse
		row.vuna_transport_task_item = cargo.name
		selected.append(row)
	entry.set("items", selected)
	entry.vuna_transport_task = task.name
	entry.stock_entry_type = entry.purpose = "Material Transfer"
	entry.add_to_transit = 1
	entry.from_warehouse = demand.source_warehouse
	entry.to_warehouse = transit_warehouse
	entry.set_transfer_qty()
	entry.calculate_rate_and_amount(raise_error_if_no_rate=False)
	return entry


def _validate_transit_warehouse(name, company, source):
	warehouse = frappe.get_doc("Warehouse", name)
	require_document_permission(warehouse)
	if (
		warehouse.company != company
		or warehouse.disabled
		or warehouse.is_group
		or warehouse.warehouse_type != "Transit"
		or warehouse.name == source
	):
		frappe.throw(_("Select an active Transit warehouse in the same company."))


def validate_dispatch_items(entry, task, demand, load):
	"""Batch/serial row splits may vary, but must preserve every planned cargo unit."""
	expected = {row.name: row for row in task.items}
	demand_items = {row.name: row for row in demand.items}
	load_items = {row.name: row for row in load.delivery_orders}
	totals = defaultdict(float)
	if not expected or not entry.items:
		frappe.throw(_("Dispatch requires planned cargo items."))
	for row in entry.items:
		cargo = expected.get(row.vuna_transport_task_item)
		planned = load_items.get(cargo.load_item) if cargo else None
		source = demand_items.get(planned.transport_demand_item) if planned else None
		if (
			not cargo
			or not source
			or (
				row.item_code != cargo.item_code
				or row.uom != cargo.uom
				or row.stock_uom != cargo.stock_uom
				or flt(row.conversion_factor) != flt(cargo.conversion_factor)
				or row.material_request != demand.source_document
				or row.material_request_item != source.source_item
				or row.s_warehouse != demand.source_warehouse
				or row.t_warehouse != entry.to_warehouse
			)
		):
			frappe.throw(_("Stock Entry rows must preserve their task, UOM, source and Material Request."))
		qty = flt(row.qty)
		if not math.isfinite(qty) or qty <= 0:
			frappe.throw(_("Dispatch quantities must be positive and finite."))
		precision = row.precision("transfer_qty")
		stock_qty = flt(qty * flt(cargo.conversion_factor), precision)
		if flt(stock_qty - flt(row.transfer_qty), precision):
			frappe.throw(_("Dispatch stock quantity does not match its UOM conversion."))
		totals[cargo.name] += stock_qty
	for cargo in task.items:
		if flt(
			totals[cargo.name] - flt(cargo.stock_qty),
			frappe.get_precision("Stock Entry Detail", "transfer_qty"),
		):
			frappe.throw(_("Dispatch must match the planned cargo. Replan the load for a short dispatch."))


def validate_dispatch_stock_entry(doc, method=None):
	previous = doc.get_doc_before_save()
	if previous and previous.get("vuna_transport_task") != doc.get("vuna_transport_task"):
		frappe.throw(_("The transport task link cannot be changed on a saved Stock Entry."))
	if not doc.get("vuna_transport_task"):
		return
	task = _lock_task(doc.vuna_transport_task)
	require_document_permission(task)
	trip, demand, load = _dispatch_context(task)
	if trip.vuna_actual_start and doc.docstatus != 1:
		frappe.throw(_("Dispatch stock cannot be edited after the trip has started."))
	if existing := get_dispatch_entry(task.name):
		if existing != doc.name:
			frappe.throw(_("This task already has an active dispatch Stock Entry: {0}.").format(existing))
	if (
		doc.company != task.company
		or doc.purpose != "Material Transfer"
		or doc.stock_entry_type != "Material Transfer"
		or not doc.add_to_transit
		or doc.outgoing_stock_entry
		or doc.from_warehouse != demand.source_warehouse
	):
		frappe.throw(_("Task dispatch must be a Material Transfer with Add to Transit enabled."))
	_validate_transit_warehouse(doc.to_warehouse, task.company, demand.source_warehouse)
	validate_dispatch_items(doc, task, demand, load)


def guard_dispatch_cancellation(doc, method=None):
	if not doc.get("vuna_transport_task"):
		return
	task = _lock_task(doc.vuna_transport_task)
	trip = frappe.get_doc("Delivery Trip", task.delivery_trip)
	if trip.vuna_actual_start:
		frappe.throw(_("Dispatch stock cannot be cancelled after departure; reconcile the transfer first."))
	if frappe.db.exists("Proof of Delivery", {"dispatch_stock_entry": doc.name, "docstatus": ["!=", 2]}):
		frappe.throw(_("Cancel or delete the linked transfer proof before cancelling dispatch stock."))


def require_dispatch_stock_posted(trip):
	if not getattr(trip, "vuna_task_based", 0):
		return
	tasks = frappe.get_all(
		"Transport Trip Task",
		filters={"delivery_trip": trip.name, "task_type": "Transfer Receipt"},
		pluck="name",
	)
	if not tasks:
		frappe.throw(
			_("The transfer trip has no receiving operations. Rebuild its delivery stops before departure.")
		)
	for task in tasks:
		_lock_task(task)
		entry = get_dispatch_entry(task)
		if not entry or frappe.db.get_value("Stock Entry", entry, "docstatus") != 1:
			frappe.throw(_("Submit the Add-to-Transit Stock Entry for every transfer before departure."))


def guard_trip_stock_cancellation(doc, method=None):
	if not getattr(doc, "vuna_task_based", 0):
		return
	frappe.db.get_value("Delivery Trip", doc.name, "name", for_update=True)
	# Cancelling the route does not reverse allocation-bound stock, even after
	# departure. Submitted PODs must first be cancelled through the normal
	# downstream cascade (enforced by prepare_delivery_trip_cancellation).
	for task in frappe.get_all("Transport Trip Task", filters={"delivery_trip": doc.name}, pluck="name"):
		_lock_task(task)
		if entry_name := get_dispatch_entry(task):
			entry = frappe.get_doc("Stock Entry", entry_name)
			# Allocation-bound stock was loaded before this trip existed. It can
			# serve a replacement trip without reversing inventory or bundles.
			if entry.get("vuna_transport_task") or not (
				entry.get("vuna_vehicle_allocation") and entry.get("vuna_transport_load")
			):
				frappe.throw(
					_(
						"Cancel or delete the legacy trip-bound dispatch Stock Entry before cancelling this trip."
					)
				)


def sync_dispatch_load_status(doc, method=None):
	if not doc.get("vuna_transport_task"):
		return
	task = _lock_task(doc.vuna_transport_task)
	from vuna_transport.services.execution.dispatch_guard_service import set_delivery_load_status

	tasks = frappe.get_all(
		"Transport Trip Task",
		filters={
			"delivery_trip": task.delivery_trip,
			"transport_load": task.transport_load,
			"task_type": "Transfer Dispatch",
		},
		pluck="name",
	)
	posted = all(
		frappe.db.get_value("Stock Entry", {"vuna_transport_task": name, "docstatus": 1}, "name")
		for name in tasks
	)
	set_delivery_load_status(
		task.transport_load,
		"Dispatched" if posted else "Ready for Dispatch",
		_("Transfer dispatch stock posting updated"),
		force=True,
	)
