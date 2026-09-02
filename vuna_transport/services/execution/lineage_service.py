"""Maintain ERPNext document lineage back to Vuna transport planning records."""

import frappe
from frappe import _
from frappe.utils import flt

from vuna_transport.services.execution.dispatch_guard_service import (
	get_locked_delivery_load,
	set_delivery_load_status,
	validate_load_is_operational,
)
from vuna_transport.services.fleet.vehicle_allocation_service import get_active_allocation


def populate_delivery_note_delivery_links(doc, method=None):
	if not doc.meta.has_field("vuna_delivery_load"):
		return
	if not doc.vuna_delivery_load:
		pick_lists = {row.against_pick_list for row in doc.items if row.against_pick_list}
		if not pick_lists:
			pick_list_items = [row.pick_list_item for row in doc.items if row.pick_list_item]
			pick_lists = set(
				frappe.get_all(
					"Pick List Item",
					filters={"name": ["in", pick_list_items]},
					pluck="parent",
				)
			) if pick_list_items else set()

		loads = {
			frappe.db.get_value("Pick List", pick_list, "vuna_delivery_load")
			for pick_list in pick_lists
		}
		loads.discard(None)
		if len(loads) == 1:
			doc.vuna_delivery_load = loads.pop()

	if not doc.vuna_delivery_load:
		return
	load = get_locked_delivery_load(doc.vuna_delivery_load)
	validate_load_is_operational(load, _("creating or updating a Delivery Note"))
	if not doc.vuna_delivery_order:
		delivery_orders = frappe.get_all(
			"Delivery Load Order",
			filters={
				"parent": load.name,
				"customer": doc.customer,
				"delivery_address": doc.shipping_address_name,
			},
			pluck="delivery_order",
		)
		if len(set(delivery_orders)) == 1:
			doc.vuna_delivery_order = delivery_orders[0]
	_validate_delivery_note_against_load(doc, load)


def _validate_delivery_note_against_load(doc, load):
	if not doc.vuna_delivery_order:
		frappe.throw(_("Delivery Note must identify its Delivery Order for Delivery Load {0}.").format(load.name))
	load_rows = frappe.get_all(
		"Delivery Load Order",
		filters={"parent": load.name, "delivery_order": doc.vuna_delivery_order},
		fields=["delivery_order_item", "allocated_quantity"],
	)
	if not load_rows:
		frappe.throw(
			_("Delivery Order {0} does not belong to Delivery Load {1}.").format(
				doc.vuna_delivery_order, load.name
			)
		)
	if doc.company != load.company:
		frappe.throw(_("Delivery Note and Delivery Load must belong to the same company."))
	warehouse_companies = {
		frappe.db.get_value("Warehouse", item.warehouse, "company")
		for item in doc.items
		if item.warehouse
	}
	warehouse_companies.discard(None)
	if warehouse_companies and warehouse_companies != {load.company}:
		frappe.throw(_("Every Delivery Note item warehouse must belong to the Delivery Load company."))

	item_allocations = {
		row.delivery_order_item: row.allocated_quantity for row in load_rows if row.delivery_order_item
	}
	order_items = frappe.get_all(
		"Delivery Order Item",
		filters={"name": ["in", list(item_allocations)]},
		fields=["name", "sales_order_item"],
	)
	allocated_by_sales_order_item = {
		row.sales_order_item: flt(item_allocations[row.name]) for row in order_items
	}
	validate_delivery_note_item_allocations(
		doc.items, allocated_by_sales_order_item, load.name
	)

	duplicate = frappe.db.get_value(
		"Delivery Note",
		{
			"name": ["!=", doc.name or ""],
			"vuna_delivery_load": load.name,
			"vuna_delivery_order": doc.vuna_delivery_order,
			"docstatus": ["!=", 2],
		},
		"name",
	)
	if duplicate:
		frappe.throw(
			_("Delivery Note {0} already exists for this Delivery Order on Delivery Load {1}.").format(
				duplicate, load.name
			)
		)


def validate_delivery_note_item_allocations(items, allocated_by_sales_order_item, load_name):
	for item in items:
		if item.so_detail not in allocated_by_sales_order_item:
			frappe.throw(
				_("Delivery Note item {0} is not allocated on Delivery Load {1}.").format(
					item.item_code, load_name
				)
			)
		if flt(item.qty) > flt(allocated_by_sales_order_item[item.so_detail]):
			frappe.throw(
				_("Delivery Note quantity for {0} exceeds its load allocation.").format(item.item_code)
			)


def validate_delivery_trip(doc, method=None):
	if not getattr(doc, "vuna_delivery_load", None):
		return
	load = get_locked_delivery_load(doc.vuna_delivery_load)
	validate_load_is_operational(load, _("creating or updating a Delivery Trip"))
	existing_trip = frappe.db.get_value(
		"Delivery Trip",
		{
			"name": ["!=", doc.name or ""],
			"vuna_delivery_load": load.name,
			"docstatus": ["!=", 2],
		},
		"name",
	)
	if existing_trip:
		frappe.throw(
			_("Delivery Load {0} already has active Delivery Trip {1}.").format(
				load.name, existing_trip
			)
		)
	if doc.company != load.company:
		frappe.throw(_("Delivery Trip and Delivery Load must belong to the same company."))
	requires_allocation = True
	if load.delivery_method:
		requires_allocation = bool(
			frappe.db.get_value(
				"Delivery Method", load.delivery_method, "requires_vehicle_allocation"
			)
		)
	allocation = get_active_allocation(load, required=requires_allocation)
	if allocation:
		if not allocation.vehicle or not allocation.driver:
			frappe.throw(_("The active Vehicle Allocation must have an ERPNext Vehicle and Driver."))
		if doc.docstatus == 0 or doc.status in ("Draft", "Scheduled"):
			if doc.vuna_vehicle_allocation != allocation.name:
				frappe.throw(_("Delivery Trip must use the active Vehicle Allocation for this load."))
			if allocation.vehicle and doc.vehicle != allocation.vehicle:
				frappe.throw(_("Delivery Trip vehicle must match Vehicle Allocation {0}.").format(allocation.name))
			if allocation.driver and doc.driver != allocation.driver:
				frappe.throw(_("Delivery Trip driver must match Vehicle Allocation {0}.").format(allocation.name))
		else:
			if doc.vuna_current_vehicle_allocation != allocation.name:
				frappe.throw(_("Current Vehicle Allocation must match the load's active allocation."))
			if doc.vuna_current_vehicle != allocation.vehicle:
				frappe.throw(_("Current Execution Vehicle must match Vehicle Allocation {0}.").format(allocation.name))
			if doc.vuna_current_driver != allocation.driver:
				frappe.throw(_("Current Execution Driver must match Vehicle Allocation {0}.").format(allocation.name))

	execution_vehicle = doc.vuna_current_vehicle or doc.vehicle
	if execution_vehicle and load.required_vehicle_category:
		vehicle_category = frappe.db.get_value("Vehicle", execution_vehicle, "vuna_vehicle_category")
		from vuna_transport.services.planning.delivery_load_service import validate_capacity

		validate_capacity(load, vehicle_category)

	stop_delivery_notes = [stop.delivery_note for stop in doc.delivery_stops if stop.delivery_note]
	expected_delivery_notes = set(
		frappe.get_all(
			"Delivery Note",
			filters={"vuna_delivery_load": load.name, "docstatus": 1},
			pluck="name",
		)
	)
	validate_delivery_trip_note_set(expected_delivery_notes, stop_delivery_notes)

	for stop in doc.delivery_stops:
		if not stop.delivery_note:
			continue
		delivery_note = frappe.db.get_value(
			"Delivery Note",
			stop.delivery_note,
			["vuna_delivery_load", "vuna_delivery_order"],
			as_dict=True,
		)
		if not delivery_note or delivery_note.vuna_delivery_load != load.name:
			frappe.throw(
				_("Delivery Note {0} does not belong to Delivery Load {1}.").format(
					stop.delivery_note, load.name
				)
			)
		stop.vuna_delivery_order = delivery_note.vuna_delivery_order
		load_rows = frappe.get_all(
			"Delivery Load Order",
			filters={
				"parent": load.name,
				"delivery_order": delivery_note.vuna_delivery_order,
			},
			pluck="name",
		)
		stop.vuna_delivery_load_order = load_rows[0] if len(load_rows) == 1 else None


def validate_delivery_trip_note_set(expected_delivery_notes, stop_delivery_notes):
	expected_delivery_notes = set(expected_delivery_notes)
	stop_delivery_note_set = set(stop_delivery_notes)
	if len(stop_delivery_notes) != len(set(stop_delivery_notes)):
		frappe.throw(_("A Delivery Note cannot appear more than once on the same Delivery Trip."))
	if not expected_delivery_notes:
		frappe.throw(_("Create and submit the Delivery Notes for this load first."))
	if stop_delivery_note_set != expected_delivery_notes:
		missing = sorted(expected_delivery_notes - stop_delivery_note_set)
		extra = sorted(stop_delivery_note_set - expected_delivery_notes)
		details = []
		if missing:
			details.append(_("missing: {0}").format(", ".join(missing)))
		if extra:
			details.append(_("not part of this load: {0}").format(", ".join(extra)))
		frappe.throw(
			_("Delivery Trip stops must exactly match the load's submitted Delivery Notes ({0}).").format(
				"; ".join(details) or _("no submitted Delivery Notes")
			)
			)


def link_delivery_trip(doc, method=None):
	if not getattr(doc, "vuna_delivery_load", None):
		return
	linked_trip = frappe.db.get_value("Delivery Load", doc.vuna_delivery_load, "delivery_trip")
	if linked_trip and linked_trip != doc.name:
		frappe.throw(
			_("Delivery Load {0} is already linked to Delivery Trip {1}.").format(
				doc.vuna_delivery_load, linked_trip
			)
		)
	if linked_trip != doc.name:
		frappe.db.set_value(
			"Delivery Load", doc.vuna_delivery_load, "delivery_trip", doc.name, update_modified=False
		)


def submit_delivery_trip(doc, method=None):
	if getattr(doc, "vuna_delivery_load", None):
		set_delivery_load_status(
			doc.vuna_delivery_load,
			"Dispatched",
			_("Delivery Trip {0} submitted").format(doc.name),
		)
		_recompute_delivery_order_statuses(doc.vuna_delivery_load)


def cancel_delivery_trip(doc, method=None):
	if getattr(doc, "vuna_delivery_load", None):
		set_delivery_load_status(
			doc.vuna_delivery_load,
			"Ready for Dispatch",
			_("Delivery Trip {0} cancelled").format(doc.name),
		)
		_recompute_delivery_order_statuses(doc.vuna_delivery_load)


def unlink_delivery_trip(doc, method=None):
	if not getattr(doc, "vuna_delivery_load", None):
		return
	if frappe.db.get_value("Delivery Load", doc.vuna_delivery_load, "delivery_trip") == doc.name:
		frappe.db.set_value(
			"Delivery Load", doc.vuna_delivery_load, "delivery_trip", None, update_modified=False
		)


def complete_load_without_trip(doc, method=None):
	load_name = getattr(doc, "vuna_delivery_load", None)
	if not load_name or doc.docstatus != 1:
		return
	load = frappe.get_doc("Delivery Load", load_name)
	if not load.delivery_method:
		return
	if frappe.db.get_value("Delivery Method", load.delivery_method, "requires_delivery_trip"):
		return
	expected = {row.delivery_order for row in load.delivery_orders if row.delivery_order}
	submitted = set(
		frappe.get_all(
			"Delivery Note",
			filters={"vuna_delivery_load": load.name, "docstatus": 1},
			pluck="vuna_delivery_order",
		)
	)
	if expected and expected.issubset(submitted):
		set_delivery_load_status(
			load, "Completed", _("All required Delivery Notes were submitted")
		)
		_recompute_delivery_order_statuses(load.name)


def reconcile_load_after_delivery_note_cancel(doc, method=None):
	load_name = getattr(doc, "vuna_delivery_load", None)
	if not load_name:
		return
	load = get_locked_delivery_load(load_name)
	if load.delivery_trip and frappe.db.get_value("Delivery Trip", load.delivery_trip, "docstatus") != 2:
		return
	if load.status in ("Completed", "Completed with Exceptions"):
		set_delivery_load_status(
			load,
			"Ready for Dispatch",
			_("Delivery Note {0} cancelled").format(doc.name),
		)
	_recompute_delivery_order_statuses(load.name)


def _recompute_delivery_order_statuses(delivery_load):
	from vuna_transport.services.execution.pod_service import recompute_delivery_order_status

	for delivery_order in _get_delivery_orders(delivery_load):
		recompute_delivery_order_status(delivery_order)


def _get_delivery_orders(delivery_load):
	return set(
		frappe.get_all(
			"Delivery Load Order",
			filters={"parent": delivery_load, "parenttype": "Delivery Load"},
			pluck="delivery_order",
		)
	)
