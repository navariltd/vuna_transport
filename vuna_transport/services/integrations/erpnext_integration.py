"""Integration services for ERPNext warehouse and delivery documents."""

import copy
from collections import defaultdict

import frappe
from frappe import _
from frappe.contacts.doctype.address.address import get_address_display
from frappe.utils import flt, get_datetime, now_datetime

from vuna_transport.services.execution.dispatch_guard_service import (
	get_locked_delivery_load,
	set_delivery_load_status,
	validate_load_is_operational,
)
from vuna_transport.services.fleet.vehicle_allocation_service import (
	get_active_allocation,
	get_allocation_load_names,
	get_allocation_loads,
)
from vuna_transport.services.shared.security_service import (
	require_doctype_permission,
	require_document_permission,
)
from vuna_transport.services.shared.workflow_policy_service import (
	allocation_is_required_before_delivery_notes,
)


def create_pick_list_for_load(load):
	"""Create a draft ERPNext Pick List for a submitted Transport Load."""
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
		delivery_order_item = frappe.get_doc("Delivery Order Item", load_row.delivery_order_item)
		if delivery_order_item.parent != load_row.delivery_order:
			frappe.throw(_("A load row references an item from a different Delivery Order."))
		pick_list.append(
			"locations",
			{
				"item_code": delivery_order_item.item_code,
				"qty": flt(load_row.allocated_quantity),
				"stock_qty": flt(load_row.allocated_quantity)
				* flt(delivery_order_item.conversion_factor or 1),
				"picked_qty": flt(load_row.allocated_quantity)
				* flt(delivery_order_item.conversion_factor or 1),
				"uom": delivery_order_item.uom or delivery_order_item.stock_uom,
				"stock_uom": delivery_order_item.stock_uom,
				"conversion_factor": delivery_order_item.conversion_factor or 1,
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
		load = frappe.get_doc("Transport Load", load)
	load = get_locked_delivery_load(load)
	validate_load_is_operational(load, _("creating Delivery Notes"))
	_repair_single_item_load_rows(load)
	# Revalidate against live Sales Order balances. A direct ERPNext Delivery Note
	# may have reduced the outstanding quantity since this load was planned.
	from vuna_transport.services.planning.load_validation_service import validate_load_allocations

	validate_load_allocations(load, for_execution=True)
	_validate_delivery_orders_for_execution(load)
	_validate_delivery_note_allocation(load)
	pick_list = frappe.get_doc("Pick List", load.pick_list) if load.pick_list else None
	if pick_list and pick_list.docstatus == 0:
		frappe.throw(_("Submit or cancel the draft Pick List before creating Delivery Notes."))
	if pick_list and pick_list.docstatus == 2:
		pick_list = None

	created = []
	for delivery_order_name, load_rows in _group_load_rows(load).items():
		if _delivery_order_has_existing_note(load.name, delivery_order_name):
			created.extend(name for name in _get_delivery_notes_for_loads([load.name]) if name not in created)
			continue

		if pick_list:
			delivery_note = _make_delivery_note_from_pick_list(delivery_order_name, load_rows, pick_list)
		else:
			delivery_note = _make_delivery_note_direct(delivery_order_name, load_rows, load.source_warehouse)
		if not delivery_note or not delivery_note.items:
			frappe.throw(_("No deliverable items were found for {0}.").format(delivery_order_name))

		_set_delivery_note_source_warehouse(delivery_note, load.source_warehouse)
		delivery_address = load_rows[0].delivery_address
		delivery_note.shipping_address_name = delivery_address
		delivery_note.shipping_address = get_address_display(
			frappe.get_doc("Address", delivery_address).as_dict()
		)
		_set_delivery_note_load_lineage(delivery_note, load_rows)
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


def create_delivery_notes_for_allocation(allocation):
	"""Create consolidated Delivery Notes for all compatible loads on an allocation.

	Items retain their exact Transport Load Item lineage, so one note can safely
	serve several Delivery Orders and loads without losing allocation history.
	"""
	load_names = get_allocation_load_names(allocation)
	if not load_names:
		frappe.throw(_("Add at least one Transport Load before creating Delivery Notes."))
	loads = [get_locked_delivery_load(name) for name in load_names]
	if len(loads) == 1:
		return create_delivery_notes_for_load(loads[0])
	existing_notes = _get_delivery_notes_for_loads(load_names)
	expected_load_orders = set(
		frappe.get_all(
			"Transport Load Item",
			filters={"parent": ["in", load_names], "parenttype": "Transport Load"},
			pluck="name",
		)
	)
	linked_load_orders = set(
		frappe.get_all(
			"Delivery Note Item",
			filters={"parent": ["in", existing_notes]},
			pluck="vuna_delivery_load_order",
		)
		if existing_notes
		else []
	)
	linked_load_orders.discard(None)
	if existing_notes and expected_load_orders.issubset(linked_load_orders):
		return existing_notes
	if any(load.pick_list for load in loads):
		# Pick Lists are load-specific; preserve the established path until a
		# single multi-load Pick List is introduced.
		created = []
		for load in loads:
			created.extend(create_delivery_notes_for_load(load))
		return created

	for load in loads:
		validate_load_is_operational(load, _("creating Delivery Notes"))
		_repair_single_item_load_rows(load)
		from vuna_transport.services.planning.load_validation_service import validate_load_allocations

		validate_load_allocations(load, for_execution=True)
		_validate_delivery_orders_for_execution(load)
		_validate_delivery_note_allocation(load)

	load_groups = []
	for load in loads:
		for delivery_order_name, load_rows in _group_load_rows(load).items():
			if _delivery_order_has_existing_note(load.name, delivery_order_name):
				continue
			delivery_note = _make_delivery_note_direct(delivery_order_name, load_rows, load.source_warehouse)
			if not delivery_note or not delivery_note.items:
				frappe.throw(_("No deliverable items were found for {0}.").format(delivery_order_name))
			_set_delivery_note_source_warehouse(delivery_note, load.source_warehouse)
			delivery_address = load_rows[0].delivery_address
			delivery_note.shipping_address_name = delivery_address
			delivery_note.shipping_address = get_address_display(
				frappe.get_doc("Address", delivery_address).as_dict()
			)
			_set_delivery_note_load_lineage(delivery_note, load_rows)
			load_groups.append((load, delivery_order_name, delivery_note, load_rows))

	if not load_groups:
		return _get_delivery_notes_for_loads(load_names)

	grouped = defaultdict(list)
	for load, delivery_order_name, delivery_note, load_rows in load_groups:
		grouped[_delivery_note_compatibility_key(delivery_note, load)].append(
			(load, delivery_order_name, delivery_note, load_rows)
		)

	created = []
	for entries in grouped.values():
		base_load, base_order, base_note, _base_rows = entries[0]
		for _source_load, _source_order, source_note, _source_rows in entries[1:]:
			for item in source_note.items:
				values = item.as_dict()
				for field in ("name", "parent", "parentfield", "parenttype", "idx", "doctype"):
					values.pop(field, None)
				base_note.append("items", values)
		base_note.vuna_delivery_load = base_load.name
		base_note.vuna_delivery_order = base_order
		base_note.save()
		created.append(base_note.name)

	for load in loads:
		requires_allocation = _delivery_method_requires_vehicle_allocation(load)
		has_allocation = bool(get_active_allocation(load, required=False))
		target_status = (
			"Awaiting Vehicle" if requires_allocation and not has_allocation else "Ready for Dispatch"
		)
		set_delivery_load_status(load, target_status, _("Delivery Notes created or confirmed for this load"))
	return created


def _delivery_note_compatibility_key(delivery_note, load):
	"""Return the conservative key used when consolidating Delivery Notes."""
	return (
		delivery_note.company,
		delivery_note.customer,
		delivery_note.shipping_address_name,
		load.source_warehouse,
		load.delivery_method,
		delivery_note.currency,
		delivery_note.conversion_rate,
		delivery_note.taxes_and_charges,
	)


def _delivery_order_has_existing_note(load_name, delivery_order_name):
	"""Check parent and item-level lineage before creating another note."""
	if frappe.db.exists(
		"Delivery Note",
		{
			"vuna_delivery_load": load_name,
			"vuna_delivery_order": delivery_order_name,
			"docstatus": ["!=", 2],
		},
	):
		return True
	return bool(
		frappe.db.sql(
			"""
			select dni.parent
			from `tabDelivery Note Item` dni
			join `tabDelivery Note` dn on dn.name = dni.parent
			join `tabTransport Load Item` dlo on dlo.name = dni.vuna_delivery_load_order
			where dlo.parent = %(load_name)s
			and dlo.delivery_order = %(delivery_order_name)s
			and dn.docstatus != 2
			limit 1
			""",
			{"load_name": load_name, "delivery_order_name": delivery_order_name},
		)
	)


def _get_delivery_notes_for_loads(load_names, docstatus=None):
	"""Find notes through parent or item-level load lineage."""
	filters = ""
	values = {"load_names": tuple(load_names)}
	if docstatus is not None:
		filters = "and dn.docstatus = %(docstatus)s"
		values["docstatus"] = docstatus
	return frappe.db.sql(
		f"""
		select distinct dn.name
		from `tabDelivery Note` dn
		left join `tabDelivery Note Item` dni on dni.parent = dn.name
		left join `tabTransport Load Item` dlo on dlo.name = dni.vuna_delivery_load_order
		where (dn.vuna_delivery_load in %(load_names)s or dlo.parent in %(load_names)s)
		{filters}
		and dn.docstatus != 2
		order by dn.creation asc
		""",
		values,
		pluck="name",
	)


def _set_delivery_note_load_lineage(delivery_note, load_rows):
	"""Link every Delivery Note Item back to its exact load-allocation row."""
	by_sales_order_item = {}
	by_item_code = {}
	for load_row in load_rows:
		if not load_row.delivery_order_item:
			continue
		delivery_order_item = frappe.get_cached_doc("Delivery Order Item", load_row.delivery_order_item)
		if delivery_order_item.sales_order_item:
			by_sales_order_item[delivery_order_item.sales_order_item] = load_row.name
		by_item_code.setdefault(delivery_order_item.item_code, []).append(load_row.name)

	for delivery_note_item in delivery_note.items:
		load_order = by_sales_order_item.get(delivery_note_item.so_detail)
		if not load_order:
			matches = by_item_code.get(delivery_note_item.item_code, [])
			load_order = matches[0] if len(matches) == 1 else None
		if load_order:
			delivery_note_item.vuna_delivery_load_order = load_order


def _validate_delivery_note_allocation(load):
	if _delivery_method_requires_vehicle_allocation(load) and allocation_is_required_before_delivery_notes():
		get_active_allocation(load, required=True)


def _delivery_method_requires_vehicle_allocation(load):
	if not load.delivery_method:
		return True
	return bool(frappe.db.get_value("Delivery Method", load.delivery_method, "requires_vehicle_allocation"))


def _make_delivery_note_from_pick_list(delivery_order_name, load_rows, pick_list):
	locations = _get_pick_list_locations(load_rows, pick_list)
	sales_orders = sorted({row.sales_order for row in locations if row.sales_order})
	if not locations or not sales_orders:
		frappe.throw(
			_("No undelivered picked items remain for Delivery Order {0}.").format(delivery_order_name)
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
			_("Delivery Order {0} must reference exactly one Sales Order.").format(delivery_order_name)
		)
	allocated_by_item = {}
	for row in load_rows:
		if row.delivery_order_item in allocated_by_item:
			frappe.throw(
				_("Delivery Order Item {0} appears more than once on Transport Load {1}.").format(
					row.delivery_order_item, row.parent or _("the selected load")
				)
			)
		allocated_by_item[row.delivery_order_item] = flt(row.allocated_quantity)
	order_items = frappe.get_all(
		"Delivery Order Item",
		filters={"name": ["in", list(allocated_by_item)]},
		fields=["name", "sales_order_item"],
	)
	allocated_by_sales_order_item = {row.sales_order_item: allocated_by_item[row.name] for row in order_items}
	delivery_note = make_delivery_note(
		next(iter(sales_orders)),
		kwargs={"filtered_children": list(allocated_by_sales_order_item)},
	)
	apply_allocated_delivery_quantities(delivery_note, allocated_by_sales_order_item)
	_set_delivery_note_source_warehouse(delivery_note, source_warehouse)
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
	"""Return a populated, unsaved Delivery Trip for a Transport Load."""
	require_doctype_permission("Delivery Trip", "create")
	load = frappe.get_doc("Transport Load", source_name)
	require_document_permission(load, "write")
	return create_delivery_trip_for_load(source_name)


def create_delivery_trip_for_load(load):
	"""Build the ERPNext Delivery Trip for every Load on the active allocation."""
	if isinstance(load, str):
		load = frappe.get_doc("Transport Load", load)
	load = get_locked_delivery_load(load)
	validate_load_is_operational(load, _("creating a Delivery Trip"))

	requires_allocation = True
	if load.delivery_method:
		requires_allocation = bool(
			frappe.db.get_value("Delivery Method", load.delivery_method, "requires_vehicle_allocation")
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
			_(
				"The active Vehicle Allocation must reference an ERPNext Driver before creating a Delivery Trip."
			)
		)

	loads = get_allocation_loads(allocation) if allocation else [load]
	load_names = [selected_load.name for selected_load in loads]
	trip_names = set(
		frappe.get_all(
			"Delivery Stop",
			filters={
				"vuna_delivery_load": ["in", load_names],
				"parenttype": "Delivery Trip",
				"docstatus": ["!=", 2],
			},
			pluck="parent",
		)
	)
	trip_names.update(selected_load.delivery_trip for selected_load in loads if selected_load.delivery_trip)
	if len(trip_names) > 1:
		frappe.throw(
			_("The selected Transport Loads are already linked to different Delivery Trips: {0}.").format(
				", ".join(sorted(trip_names))
			)
		)
	load_names = [selected_load.name for selected_load in loads]
	delivery_notes = _get_delivery_notes_for_loads(load_names, docstatus=1)
	if not delivery_notes:
		frappe.throw(_("Create and submit the Delivery Notes for this load first."))
	expected_delivery_orders = {
		row.delivery_order
		for selected_load in loads
		for row in selected_load.delivery_orders
		if row.delivery_order
	}
	note_delivery_orders = set(
		frappe.get_all(
			"Delivery Note",
			filters={"name": ["in", delivery_notes]},
			fields=["vuna_delivery_order"],
			pluck="vuna_delivery_order",
		)
	)
	note_delivery_orders.update(
		frappe.get_all(
			"Transport Load Item",
			filters={
				"name": [
					"in",
					frappe.get_all(
						"Delivery Note Item",
						filters={"parent": ["in", delivery_notes]},
						pluck="vuna_delivery_load_order",
					),
				]
			},
			fields=["delivery_order"],
			pluck="delivery_order",
		)
	)
	note_delivery_orders.discard(None)
	if expected_delivery_orders != note_delivery_orders:
		missing = sorted(expected_delivery_orders - note_delivery_orders)
		frappe.throw(
			_("Submitted Delivery Notes are missing for Delivery Orders: {0}.").format(", ".join(missing))
		)
	from vuna_transport.services.execution.lineage_service import validate_delivery_note_quantities_for_loads

	validate_delivery_note_quantities_for_loads(loads)

	if trip_names:
		existing_trip_name = next(iter(trip_names))
		linked_load_names = set(
			frappe.get_all(
				"Delivery Stop",
				filters={"parent": existing_trip_name, "parenttype": "Delivery Trip"},
				pluck="vuna_delivery_load",
			)
		)
		linked_load_names.discard(None)
		if linked_load_names != set(load_names):
			frappe.throw(
				_(
					"The active Vehicle Allocation contains loads that are not all assigned to Delivery Trip {0}. "
					"Review the allocation before continuing."
				).format(existing_trip_name)
			)
		existing_trip = frappe.get_doc("Delivery Trip", existing_trip_name)
		if existing_trip.docstatus != 2:
			return existing_trip

	from erpnext.stock.doctype.delivery_note.delivery_note import make_delivery_trip

	delivery_trip = frappe.new_doc("Delivery Trip")
	for delivery_note in delivery_notes:
		delivery_trip = make_delivery_trip(delivery_note, delivery_trip)

	delivery_trip.company = load.company
	if allocation:
		delivery_trip.vehicle = allocation.vehicle
		delivery_trip.driver = allocation.driver
	departures = [
		get_datetime(selected_load.planned_departure)
		for selected_load in loads
		if selected_load.planned_departure
	]
	delivery_trip.departure_time = min(departures) if departures else now_datetime()
	delivery_trip.vuna_vehicle_allocation = allocation.name if allocation else None
	delivery_trip.vuna_current_vehicle_allocation = allocation.name if allocation else None
	delivery_trip.vuna_current_vehicle = allocation.vehicle if allocation else None
	delivery_trip.vuna_current_driver = allocation.driver if allocation else None
	delivery_trip.vuna_reallocation_count = 0
	_populate_delivery_stop_links(delivery_trip, loads)
	return delivery_trip


def _populate_delivery_stop_links(delivery_trip, loads):
	for stop in delivery_trip.delivery_stops:
		delivery_note = frappe.db.get_value(
			"Delivery Note", stop.delivery_note, ["vuna_delivery_order", "vuna_delivery_load"], as_dict=True
		)
		if not delivery_note:
			continue
		delivery_order = delivery_note.vuna_delivery_order
		load = next((row for row in loads if row.name == delivery_note.vuna_delivery_load), None)
		if not load:
			load_order = frappe.db.get_value(
				"Delivery Note Item",
				{"parent": stop.delivery_note, "vuna_delivery_load_order": ["is", "set"]},
				"vuna_delivery_load_order",
			)
			load_name = (
				frappe.db.get_value("Transport Load Item", load_order, "parent") if load_order else None
			)
			load = next((row for row in loads if row.name == load_name), None)
			if load and load_order:
				delivery_order = frappe.db.get_value("Transport Load Item", load_order, "delivery_order")
		load_rows = [
			row for row in (load.delivery_orders if load else []) if row.delivery_order == delivery_order
		]
		stop.vuna_delivery_order = delivery_order
		stop.vuna_delivery_load = delivery_note.vuna_delivery_load
		if len(load_rows) == 1:
			stop.vuna_delivery_load_order = load_rows[0].name


def _validate_delivery_orders_for_execution(load):
	for delivery_order_name in {row.delivery_order for row in load.delivery_orders if row.delivery_order}:
		delivery_order = frappe.db.get_value(
			"Delivery Order",
			delivery_order_name,
			["docstatus", "status", "company", "source_warehouse", "sales_order"],
			as_dict=True,
		)
		if not delivery_order or delivery_order.docstatus != 1:
			frappe.throw(
				_("Delivery Order {0} is no longer eligible for execution.").format(delivery_order_name)
			)
		sales_order_name = delivery_order.sales_order
		sales_order = frappe.db.get_value(
			"Sales Order", sales_order_name, ["docstatus", "status"], as_dict=True
		)
		if sales_order and (sales_order.docstatus == 2 or sales_order.status in ("Closed", "Cancelled")):
			frappe.throw(
				_("Delivery Order {0} cannot be executed because its Sales Order is {1}.").format(
					delivery_order_name, sales_order.status or "Cancelled"
				)
			)
		if delivery_order.status in ("Cancelled", "Delivered", "Closed"):
			frappe.throw(
				_("Delivery Order {0} is {1} and cannot be executed.").format(
					delivery_order_name, delivery_order.status
				)
			)
		if delivery_order.company != load.company:
			frappe.throw(_("Delivery Order {0} belongs to a different company.").format(delivery_order_name))


def _validate_delivery_note_warehouses(delivery_note, source_warehouse):
	companies = {
		frappe.db.get_value("Warehouse", item.warehouse, "company")
		for item in delivery_note.items
		if item.warehouse
	}
	companies.discard(None)
	if source_warehouse:
		load_company = frappe.db.get_value("Warehouse", source_warehouse, "company")
		if load_company and companies and companies != {load_company}:
			frappe.throw(_("Delivery Note item warehouses must belong to the load company."))


def _set_delivery_note_source_warehouse(delivery_note, source_warehouse):
	"""Use the load's dispatch warehouse for generated stock rows.

	Users may still change individual Delivery Note item warehouses afterwards;
	ERPNext remains authoritative for the final stock movement.
	"""
	if not source_warehouse:
		return
	for item in delivery_note.items:
		item.warehouse = source_warehouse


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
			frappe.throw(_("Load row {0} must reference a Delivery Order Item.").format(row.idx))
		item = delivery_order.items[0]
		row.delivery_order_item = item.name
		frappe.db.set_value(
			"Transport Load Item",
			row.name,
			"delivery_order_item",
			item.name,
			update_modified=False,
		)


def _get_pick_list_locations(load_rows, pick_list):
	item_keys = set()
	for row in load_rows:
		delivery_order_item = frappe.get_doc("Delivery Order Item", row.delivery_order_item)
		item_keys.add((row.sales_order, delivery_order_item.sales_order_item, delivery_order_item.item_code))
	return [
		location
		for location in pick_list.locations
		if (location.sales_order, location.sales_order_item, location.item_code) in item_keys
		and flt(location.picked_qty) - flt(location.delivered_qty) > 0
	]
