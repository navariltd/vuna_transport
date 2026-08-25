"""Integration services for ERPNext warehouse and delivery documents."""

import copy
from collections import defaultdict

import frappe
from frappe import _
from frappe.contacts.doctype.address.address import get_address_display
from frappe.utils import flt, get_datetime, now_datetime

from vuna_transport.services.fleet.vehicle_allocation_service import get_active_allocation
from vuna_transport.services.execution.dispatch_guard_service import (
	get_locked_delivery_load,
	set_delivery_load_status,
	validate_load_is_operational,
)
from vuna_transport.services.shared.security_service import (
	require_doctype_permission,
	require_document_permission,
)
from vuna_transport.services.shared.workflow_policy_service import (
	allocation_is_required_before_delivery_notes,
)


def create_pick_list_for_load(load):
	"""Create a draft ERPNext Pick List for a submitted Delivery Load."""
	load = get_locked_delivery_load(load)
	validate_load_is_operational(load, _("creating a Pick List"))
	if load.pick_list:
		existing_pick_list = frappe.get_doc("Pick List", load.pick_list)
		if existing_pick_list.docstatus != 2:
			return existing_pick_list
		load.db_set("pick_list", None, update_modified=False)

	_repair_single_item_load_rows(load)
	pick_list = frappe.new_doc("Pick List")
	pick_list.company = load.company
	pick_list.vuna_delivery_load = load.name
	pick_list.purpose = "Delivery"
	pick_list.parent_warehouse = load.source_warehouse
	pick_list.pick_manually = 1
	pick_list.group_same_items = 1

	for load_row in load.delivery_orders:
		delivery_order_item = frappe.get_doc(
			"Delivery Order Item", load_row.delivery_order_item
		)
		if delivery_order_item.parent != load_row.delivery_order:
			frappe.throw(_("A load row references an item from a different Delivery Order."))
		pick_list.append(
			"locations",
			{
				"item_code": delivery_order_item.item_code,
				"qty": flt(load_row.allocated_quantity),
				"stock_qty": flt(load_row.allocated_quantity),
				"picked_qty": flt(load_row.allocated_quantity),
				"uom": delivery_order_item.stock_uom,
				"stock_uom": delivery_order_item.stock_uom,
				"warehouse": load.source_warehouse,
				"sales_order": load_row.sales_order,
				"sales_order_item": delivery_order_item.sales_order_item,
			},
		)

	pick_list.insert()
	load.db_set("pick_list", pick_list.name, update_modified=False)
	set_delivery_load_status(load, "Released to Warehouse", _("Pick List {0} created").format(pick_list.name))
	return pick_list


def create_delivery_notes_for_load(load):
	"""Create one draft Delivery Note per Delivery Order using direct or picked fulfilment."""
	if isinstance(load, str):
		load = frappe.get_doc("Delivery Load", load)
	load = get_locked_delivery_load(load)
	validate_load_is_operational(load, _("creating Delivery Notes"))
	_repair_single_item_load_rows(load)
	_validate_delivery_orders_for_execution(load)
	_validate_delivery_note_allocation(load)
	pick_list = frappe.get_doc("Pick List", load.pick_list) if load.pick_list else None
	if pick_list and pick_list.docstatus == 0:
		frappe.throw(_("Submit or cancel the draft Pick List before creating Delivery Notes."))
	if pick_list and pick_list.docstatus == 2:
		pick_list = None

	created = []
	for delivery_order_name, load_rows in _group_load_rows(load).items():
		existing = frappe.db.get_value(
			"Delivery Note",
			{
				"vuna_delivery_load": load.name,
				"vuna_delivery_order": delivery_order_name,
				"docstatus": ["!=", 2],
			},
		)
		if existing:
			created.append(existing)
			continue

		if pick_list:
			delivery_note = _make_delivery_note_from_pick_list(
				delivery_order_name, load_rows, pick_list
			)
		else:
			delivery_note = _make_delivery_note_direct(
				delivery_order_name, load_rows, load.source_warehouse
			)
		if not delivery_note or not delivery_note.items:
			frappe.throw(_("No deliverable items were found for {0}.").format(delivery_order_name))

		delivery_address = load_rows[0].delivery_address
		delivery_note.shipping_address_name = delivery_address
		delivery_note.shipping_address = get_address_display(
			frappe.get_doc("Address", delivery_address).as_dict()
		)
		delivery_note.vuna_delivery_load = load.name
		delivery_note.vuna_delivery_order = delivery_order_name
		delivery_note.save()
		created.append(delivery_note.name)

	requires_allocation = _delivery_method_requires_vehicle_allocation(load)
	has_allocation = bool(get_active_allocation(load, required=False))
	target_status = "Awaiting Vehicle" if requires_allocation and not has_allocation else "Ready for Dispatch"
	set_delivery_load_status(
		load,
		target_status,
		_("Delivery Notes created or confirmed for this load"),
	)
	return created


def _validate_delivery_note_allocation(load):
	if (
		_delivery_method_requires_vehicle_allocation(load)
		and allocation_is_required_before_delivery_notes()
	):
		get_active_allocation(load, required=True)


def _delivery_method_requires_vehicle_allocation(load):
	if not load.delivery_method:
		return True
	return bool(
		frappe.db.get_value(
			"Delivery Method", load.delivery_method, "requires_vehicle_allocation"
		)
	)


def _make_delivery_note_from_pick_list(delivery_order_name, load_rows, pick_list):
	locations = _get_pick_list_locations(load_rows, pick_list)
	sales_orders = sorted({row.sales_order for row in locations if row.sales_order})
	if not locations or not sales_orders:
		frappe.throw(
			_("No undelivered picked items remain for Delivery Order {0}.").format(
				delivery_order_name
			)
		)

	from erpnext.stock.doctype.pick_list.pick_list import create_delivery_from_so

	stop_pick_list = copy.copy(pick_list)
	stop_pick_list.locations = locations
	return create_delivery_from_so(stop_pick_list, sales_orders, "Delivery Note")


def _make_delivery_note_direct(delivery_order_name, load_rows, source_warehouse):
	from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

	sales_orders = {row.sales_order for row in load_rows if row.sales_order}
	if len(sales_orders) != 1:
		frappe.throw(
			_("Delivery Order {0} must reference exactly one Sales Order.").format(
				delivery_order_name
			)
		)
	allocated_by_item = {
		row.delivery_order_item: flt(row.allocated_quantity) for row in load_rows
	}
	order_items = frappe.get_all(
		"Delivery Order Item",
		filters={"name": ["in", list(allocated_by_item)]},
		fields=["name", "sales_order_item"],
	)
	allocated_by_sales_order_item = {
		row.sales_order_item: allocated_by_item[row.name] for row in order_items
	}
	delivery_note = make_delivery_note(
		next(iter(sales_orders)),
		kwargs={"filtered_children": list(allocated_by_sales_order_item)},
	)
	apply_allocated_delivery_quantities(delivery_note, allocated_by_sales_order_item)
	_validate_delivery_note_warehouses(delivery_note, source_warehouse)
	delivery_note.calculate_taxes_and_totals()
	return delivery_note


def apply_allocated_delivery_quantities(delivery_note, allocated_by_sales_order_item):
	"""Limit mapped Delivery Note rows to the quantities assigned to this load."""
	for item in delivery_note.items:
		if item.so_detail not in allocated_by_sales_order_item:
			continue
		item.qty = allocated_by_sales_order_item[item.so_detail]
		item.stock_qty = flt(item.qty) * flt(item.conversion_factor or 1)
		item.amount = flt(item.qty) * flt(item.rate)
		item.base_amount = flt(item.qty) * flt(item.base_rate)


@frappe.whitelist()
def make_delivery_trip_from_load(source_name):
	"""Return a populated, unsaved Delivery Trip for a Delivery Load."""
	require_doctype_permission("Delivery Trip", "create")
	load = frappe.get_doc("Delivery Load", source_name)
	require_document_permission(load, "write")
	return create_delivery_trip_for_load(source_name)


def create_delivery_trip_for_load(load):
	"""Build the ERPNext Delivery Trip that executes a Delivery Load."""
	if isinstance(load, str):
		load = frappe.get_doc("Delivery Load", load)
	load = get_locked_delivery_load(load)
	validate_load_is_operational(load, _("creating a Delivery Trip"))
	existing_trip_name = load.delivery_trip or frappe.db.get_value(
		"Delivery Trip",
		{"vuna_delivery_load": load.name, "docstatus": ["!=", 2]},
		"name",
	)
	if existing_trip_name:
		existing_trip = frappe.get_doc("Delivery Trip", existing_trip_name)
		if existing_trip.docstatus != 2:
			return existing_trip
		load.db_set("delivery_trip", None, update_modified=False)

	requires_allocation = True
	if load.delivery_method:
		requires_allocation = bool(
			frappe.db.get_value(
				"Delivery Method", load.delivery_method, "requires_vehicle_allocation"
			)
		)
	allocation = get_active_allocation(load, required=requires_allocation)
	if allocation and not allocation.vehicle:
		frappe.throw(
			_(
				"The active subcontractor allocation must reference an ERPNext Vehicle before creating a Delivery Trip."
			)
		)
	if allocation and not allocation.driver:
		frappe.throw(
			_("The active Vehicle Allocation must reference an ERPNext Driver before creating a Delivery Trip.")
		)

	delivery_note_rows = frappe.get_all(
		"Delivery Note",
		filters={"vuna_delivery_load": load.name, "docstatus": 1},
		fields=["name", "vuna_delivery_order"],
	)
	delivery_notes = [row.name for row in delivery_note_rows]
	if not delivery_notes:
		frappe.throw(_("Create and submit the Delivery Notes for this load first."))
	expected_delivery_orders = {row.delivery_order for row in load.delivery_orders if row.delivery_order}
	note_delivery_orders = {row.vuna_delivery_order for row in delivery_note_rows if row.vuna_delivery_order}
	if expected_delivery_orders != note_delivery_orders:
		missing = sorted(expected_delivery_orders - note_delivery_orders)
		frappe.throw(
			_("Submitted Delivery Notes are missing for Delivery Orders: {0}.").format(
				", ".join(missing)
			)
		)

	from erpnext.stock.doctype.delivery_note.delivery_note import make_delivery_trip

	delivery_trip = frappe.new_doc("Delivery Trip")
	for delivery_note in delivery_notes:
		delivery_trip = make_delivery_trip(delivery_note, delivery_trip)

	delivery_trip.company = load.company
	if allocation:
		delivery_trip.vehicle = allocation.vehicle
		delivery_trip.driver = allocation.driver
	delivery_trip.departure_time = (
		get_datetime(load.planned_departure) if load.planned_departure else now_datetime()
	)
	delivery_trip.vuna_delivery_load = load.name
	delivery_trip.vuna_vehicle_allocation = allocation.name if allocation else None
	delivery_trip.vuna_current_vehicle_allocation = allocation.name if allocation else None
	delivery_trip.vuna_current_vehicle = allocation.vehicle if allocation else None
	delivery_trip.vuna_current_driver = allocation.driver if allocation else None
	delivery_trip.vuna_reallocation_count = 0
	_populate_delivery_stop_links(delivery_trip, load)
	return delivery_trip


def _populate_delivery_stop_links(delivery_trip, load):
	for stop in delivery_trip.delivery_stops:
		delivery_order = frappe.db.get_value(
			"Delivery Note", stop.delivery_note, "vuna_delivery_order"
		)
		load_rows = [
			row for row in load.delivery_orders if row.delivery_order == delivery_order
		]
		stop.vuna_delivery_order = delivery_order
		if len(load_rows) == 1:
			stop.vuna_delivery_load_order = load_rows[0].name


def _validate_delivery_orders_for_execution(load):
	for delivery_order_name in {row.delivery_order for row in load.delivery_orders if row.delivery_order}:
		delivery_order = frappe.db.get_value(
			"Delivery Order",
			delivery_order_name,
			["docstatus", "status", "company", "source_warehouse"],
			as_dict=True,
		)
		if not delivery_order or delivery_order.docstatus != 1 or delivery_order.status in (
			"Cancelled",
			"Delivered",
		):
			frappe.throw(
				_("Delivery Order {0} is no longer eligible for execution.").format(
					delivery_order_name
				)
			)
		if delivery_order.company != load.company:
			frappe.throw(_("Delivery Order {0} belongs to a different company.").format(delivery_order_name))
		if delivery_order.source_warehouse != load.source_warehouse:
			frappe.throw(
				_("Delivery Order {0} uses a different source warehouse.").format(
					delivery_order_name
				)
			)


def _validate_delivery_note_warehouses(delivery_note, source_warehouse):
	invalid_items = [
		item.item_code for item in delivery_note.items if item.warehouse != source_warehouse
	]
	if invalid_items:
		frappe.throw(
			_("Delivery Note items must use source warehouse {0}: {1}.").format(
				source_warehouse, ", ".join(sorted(set(invalid_items)))
			)
		)


def _group_load_rows(load):
	groups = defaultdict(list)
	for row in load.delivery_orders:
		groups[row.delivery_order].append(row)
	return groups


def _repair_single_item_load_rows(load):
	for row in load.delivery_orders:
		if row.delivery_order_item:
			continue
		delivery_order = frappe.get_doc("Delivery Order", row.delivery_order)
		if len(delivery_order.items) != 1:
			frappe.throw(
				_("Load row {0} must reference a Delivery Order Item.").format(row.idx)
			)
		item = delivery_order.items[0]
		row.delivery_order_item = item.name
		frappe.db.set_value(
			"Delivery Load Order",
			row.name,
			"delivery_order_item",
			item.name,
			update_modified=False,
		)


def _get_pick_list_locations(load_rows, pick_list):
	item_keys = set()
	for row in load_rows:
		delivery_order_item = frappe.get_doc(
			"Delivery Order Item", row.delivery_order_item
		)
		item_keys.add((row.sales_order, delivery_order_item.sales_order_item, delivery_order_item.item_code))
	return [
		location
		for location in pick_list.locations
		if (location.sales_order, location.sales_order_item, location.item_code) in item_keys
		and flt(location.picked_qty) - flt(location.delivered_qty) > 0
	]
