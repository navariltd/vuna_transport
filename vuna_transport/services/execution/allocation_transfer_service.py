"""Prepare warehouse-transfer stock from allocation, before trip planning."""

import math
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

from vuna_transport.services.shared.security_service import (
	require_doctype_permission,
	require_document_permission,
)


def allocation_stock_entry(allocation, load):
	return frappe.db.get_value(
		"Stock Entry",
		{"vuna_vehicle_allocation": allocation, "vuna_transport_load": load, "docstatus": ["!=", 2]},
		"name",
	)


def resolve_vehicle_transit_warehouse(allocation, check_permissions=True):
	if not frappe.get_meta("Warehouse").has_field("vehicle"):
		frappe.throw(_("Configure a Vehicle link on Warehouse before preparing vehicle transit stock."))
	names = frappe.get_all(
		"Warehouse",
		filters={
			"company": allocation.company,
			"vehicle": allocation.vehicle,
			"warehouse_type": "Transit",
			"is_group": 0,
			"disabled": 0,
		},
		pluck="name",
		limit_page_length=2,
	)
	if len(names) != 1:
		frappe.throw(
			_("Link exactly one active Transit Warehouse to Vehicle {0} in this company.").format(
				allocation.vehicle
			)
		)
	warehouse = frappe.get_doc("Warehouse", names[0])
	if check_permissions:
		require_document_permission(warehouse)
	return warehouse.name


def _context(allocation_name, load_name):
	frappe.db.get_value("Vehicle Allocation", allocation_name, "name", for_update=True)
	allocation = frappe.get_doc("Vehicle Allocation", allocation_name)
	load = frappe.get_doc("Transport Load", load_name)
	from vuna_transport.services.fleet.vehicle_allocation_service import get_allocation_load_names
	from vuna_transport.services.planning.transport_load_service import is_demand_load

	if (
		allocation.docstatus != 1
		or load.docstatus != 1
		or load.name not in get_allocation_load_names(allocation)
		or allocation.company != load.company
		or not allocation.vehicle
		or not allocation.driver
		or not is_demand_load(load)
		or load.active_vehicle_allocation != allocation.name
	):
		frappe.throw(_("Select an active submitted allocation and one of its transfer loads."))
	for doc in (allocation, load):
		require_document_permission(doc)
	return allocation, load


def set_allocation_stock_defaults(entry, allocation):
	for doc in [entry, *entry.items]:
		for field, value in (("vehicle", allocation.vehicle), ("driver", allocation.driver)):
			if doc.meta.has_field(field):
				doc.set(field, value)


@frappe.whitelist()
def make_allocation_stock_entry(source_name: str, load_name: str):
	require_doctype_permission("Stock Entry", "create")
	allocation, load = _context(source_name, load_name)
	if load.movement_type == "Supplier Pickup":
		frappe.throw(
			_(
				"Supplier collections enter vehicle stock through Purchase Receipt after pickup, not a pre-trip Stock Entry."
			)
		)
	from vuna_transport.services.demand.depot_return_demand_service import is_return_load

	if is_return_load(load):
		frappe.throw(_("Depot return stock is posted when the driver submits the pickup POD."))
	if existing := allocation_stock_entry(allocation.name, load.name):
		entry = frappe.get_doc("Stock Entry", existing)
		require_document_permission(entry)
		return entry
	if load.delivery_trip:
		frappe.throw(_("Prepare transfer stock before creating the trip."))
	if frappe.db.exists("Stock Entry", {"vuna_transport_load": load.name, "docstatus": ["!=", 2]}):
		frappe.throw(
			_(
				"This load already has dispatch stock under another allocation. Reconcile that entry before preparing new stock."
			)
		)
	transit = resolve_vehicle_transit_warehouse(allocation)
	from erpnext.stock.doctype.material_request.material_request import make_stock_entry

	entry = frappe.new_doc("Stock Entry")
	entry.company = allocation.company
	entry.purpose = entry.stock_entry_type = "Material Transfer"
	entry.add_to_transit = 1
	entry.from_warehouse, entry.to_warehouse = load.source_warehouse, transit
	entry.vuna_vehicle_allocation, entry.vuna_transport_load = allocation.name, load.name
	templates = {}
	for demand_name in dict.fromkeys(row.transport_demand for row in load.delivery_orders):
		demand = frappe.get_doc("Transport Demand", demand_name)
		require_document_permission(demand)
		if demand.docstatus != 1 or demand.source_document_type != "Material Request":
			frappe.throw(_("Transfer stock requires submitted Material Request demand."))
		mapped = make_stock_entry(demand.source_document)
		by_source = {row.material_request_item: row for row in mapped.items}
		for item in demand.items:
			templates[item.name] = by_source.get(item.source_item)
	for cargo in load.delivery_orders:
		source = templates.get(cargo.transport_demand_item)
		if not source or flt(source.transfer_qty) < flt(cargo.allocated_stock_qty):
			frappe.throw(_("The Material Request no longer has enough outstanding quantity."))
		row = frappe.copy_doc(source)
		row.vuna_transport_load_item = cargo.name
		row.vuna_transport_task_item = None
		row.qty, row.uom, row.conversion_factor = cargo.allocated_quantity, cargo.uom, cargo.conversion_factor
		row.stock_uom, row.transfer_qty = cargo.stock_uom, cargo.allocated_stock_qty
		row.s_warehouse, row.t_warehouse = load.source_warehouse, transit
		entry.append("items", row)
	set_allocation_stock_defaults(entry, allocation)
	entry.set_missing_values()
	entry.set_transfer_qty()
	entry.calculate_rate_and_amount(raise_error_if_no_rate=False)
	return entry


def validate_allocation_stock_entry(doc, method=None):
	previous = doc.get_doc_before_save()
	for field in ("vuna_vehicle_allocation", "vuna_transport_load"):
		if previous and previous.get(field) != doc.get(field):
			frappe.throw(_("Allocation stock lineage cannot be changed on a saved entry."))
	if not doc.get("vuna_vehicle_allocation") and not doc.get("vuna_transport_load"):
		return
	allocation, load = _context(doc.vuna_vehicle_allocation, doc.vuna_transport_load)
	if (
		doc.company != allocation.company
		or not doc.add_to_transit
		or doc.purpose != "Material Transfer"
		or doc.outgoing_stock_entry
		or doc.get("vuna_transport_task")
		or doc.get("vuna_transport_proof")
		or doc.from_warehouse != load.source_warehouse
		or doc.to_warehouse != resolve_vehicle_transit_warehouse(allocation)
	):
		frappe.throw(
			_("Allocation dispatch must move its cargo to the allocated vehicle's Transit Warehouse.")
		)
	if existing := allocation_stock_entry(allocation.name, load.name):
		if existing != doc.name:
			frappe.throw(_("This allocation/load already has an active Stock Entry: {0}.").format(existing))
	if load.delivery_trip and doc.docstatus != 1:
		frappe.throw(_("Dispatch cargo cannot be edited after trip creation."))
	expected = {row.name: row for row in load.delivery_orders}
	totals = defaultdict(float)
	precision = frappe.get_precision("Stock Entry Detail", "transfer_qty")
	for row in doc.items:
		cargo = expected.get(row.vuna_transport_load_item)
		if not cargo:
			frappe.throw(_("Every dispatch row must reference cargo from this Transport Load."))
		source = frappe.get_doc("Transport Demand Item", cargo.transport_demand_item)
		demand = frappe.get_doc("Transport Demand", cargo.transport_demand)
		quantity = flt(row.qty)
		if (
			not math.isfinite(quantity)
			or quantity <= 0
			or row.item_code != cargo.item_code
			or row.uom != cargo.uom
			or row.stock_uom != cargo.stock_uom
			or flt(row.conversion_factor) != flt(cargo.conversion_factor)
			or row.s_warehouse != load.source_warehouse
			or row.t_warehouse != doc.to_warehouse
			or row.material_request != demand.source_document
			or row.material_request_item != source.source_item
			or flt(flt(row.transfer_qty) - quantity * flt(cargo.conversion_factor), precision)
		):
			frappe.throw(
				_(
					"Dispatch rows must preserve load quantities, UOM, warehouses and Material Request lineage."
				)
			)
		totals[cargo.name] += flt(row.transfer_qty)
	if set(totals) != set(expected) or any(
		flt(totals[name] - flt(cargo.allocated_stock_qty), precision) for name, cargo in expected.items()
	):
		frappe.throw(
			_("Dispatch must include exactly the allocated cargo. Replan short dispatch quantities.")
		)
	set_allocation_stock_defaults(doc, allocation)


def sync_allocation_stock(doc, method=None):
	if not doc.get("vuna_vehicle_allocation"):
		return
	from vuna_transport.services.execution.dispatch_guard_service import set_delivery_load_status
	from vuna_transport.services.fleet.vehicle_allocation_service import sync_allocation_execution_status

	set_delivery_load_status(
		doc.vuna_transport_load,
		"Dispatched" if doc.docstatus == 1 else "Ready for Dispatch",
		_("Allocation transfer stock posting updated"),
		force=True,
	)
	sync_allocation_execution_status(doc.vuna_vehicle_allocation)


def guard_allocation_stock_cancellation(doc, method=None):
	if not doc.get("vuna_vehicle_allocation"):
		return
	trip = frappe.db.get_value("Transport Load", doc.vuna_transport_load, "delivery_trip")
	if trip:
		started = frappe.db.get_value("Delivery Trip", trip, "vuna_actual_start", for_update=True)
		if started:
			frappe.throw(_("Dispatch stock cannot be cancelled after departure."))
	if frappe.db.exists("Proof of Delivery", {"dispatch_stock_entry": doc.name, "docstatus": ["!=", 2]}):
		frappe.throw(_("Cancel or delete transfer proof before cancelling its dispatch stock."))
