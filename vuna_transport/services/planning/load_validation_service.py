"""Authoritative Delivery Load compatibility, reservation, and quantity rules."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

from vuna_transport.services.demand.delivery_method_service import (
	canonical_delivery_method,
	delivery_methods_are_compatible,
)

PLANNABLE_DELIVERY_ORDER_STATUSES = ("Open", "Partially Allocated")


def validate_load_allocations(load, lock=True, for_execution=False):
	"""Validate and rebuild every load row from authoritative Delivery Order data."""
	if not load.delivery_orders:
		frappe.throw(_("At least one Delivery Order is required."))

	item_names = sorted({row.delivery_order_item for row in load.delivery_orders if row.delivery_order_item})
	if lock:
		_lock_delivery_order_items(item_names)
	available_quantities = get_available_quantities(item_names, exclude_load=load.name)

	delivery_orders = {}
	delivery_order_items = {}
	current_allocations = defaultdict(float)
	seen_item_rows = set()
	first_order = None

	for row in load.delivery_orders:
		_validate_required_row_links(row)
		item_key = (row.delivery_order, row.delivery_order_item)
		if item_key in seen_item_rows:
			frappe.throw(
				_("Delivery Order Item {0} appears more than once in this Delivery Load.").format(
					row.delivery_order_item
				)
			)
		seen_item_rows.add(item_key)
		delivery_order = delivery_orders.get(row.delivery_order)
		if not delivery_order:
			delivery_order = frappe.get_doc("Delivery Order", row.delivery_order)
			delivery_orders[row.delivery_order] = delivery_order
		if first_order is None:
			first_order = delivery_order
		_validate_delivery_order_for_load(
			load, delivery_order, first_order, for_execution=for_execution
		)

		delivery_order_item = delivery_order_items.get(row.delivery_order_item)
		if not delivery_order_item:
			delivery_order_item = frappe.get_doc("Delivery Order Item", row.delivery_order_item)
			delivery_order_items[row.delivery_order_item] = delivery_order_item
		if delivery_order_item.parent != delivery_order.name:
			frappe.throw(
				_("Delivery Order Item {0} does not belong to Delivery Order {1}.").format(
					delivery_order_item.name, delivery_order.name
				)
			)

		key = (delivery_order.name, delivery_order_item.name)
		current_allocations[key] += flt(row.allocated_quantity)
		_populate_load_row(
			row,
			delivery_order,
			delivery_order_item,
			available_quantities.get(delivery_order_item.name, 0),
		)

	for (_delivery_order_name, delivery_order_item_name), current_quantity in current_allocations.items():
		available_quantity = available_quantities.get(delivery_order_item_name, 0)
		if current_quantity > available_quantity:
			frappe.throw(
				_("Allocation for {0} exceeds the available transport quantity by {1}.").format(
					delivery_order_item_name, current_quantity - available_quantity
				)
			)


def _lock_delivery_order_items(item_names):
	if not item_names:
		return
	placeholders = ", ".join(["%s"] * len(item_names))
	frappe.db.sql(
		f"select name from `tabDelivery Order Item` where name in ({placeholders}) order by name for update",
		tuple(item_names),
	)


def _validate_required_row_links(row):
	if not row.delivery_order:
		frappe.throw(_("Every load row must reference a Delivery Order."))
	if not row.delivery_order_item:
		frappe.throw(_("Every load row must reference a Delivery Order Item."))
	if flt(row.allocated_quantity) <= 0:
		frappe.throw(_("Allocated quantity must be greater than zero."))


def _validate_delivery_order_for_load(load, delivery_order, first_order, for_execution=False):
	from vuna_transport.services.demand.delivery_order_service import sync_delivery_order_quantities

	# Re-read Sales Order quantities before accepting a stale Delivery Order into a load.
	# This catches goods delivered directly from the Sales Order outside Vuna planning.
	sync_delivery_order_quantities(delivery_order, persist=False)
	if delivery_order.docstatus != 1:
		frappe.throw(
			_("Delivery Order {0} must be submitted before it can be loaded.").format(delivery_order.name)
		)
	sales_order_state = frappe.db.get_value(
		"Sales Order",
		delivery_order.sales_order,
		["docstatus", "status"],
		as_dict=True,
	)
	if sales_order_state and (
		sales_order_state.docstatus == 2 or sales_order_state.status in ("Closed", "Cancelled")
	):
		frappe.throw(
			_("Delivery Order {0} cannot be loaded because Sales Order {1} is {2}.").format(
				delivery_order.name, delivery_order.sales_order, sales_order_state.status or "Cancelled"
			)
		)
	allowed_statuses = PLANNABLE_DELIVERY_ORDER_STATUSES
	if for_execution:
		allowed_statuses = (*allowed_statuses, "Allocated", "Ready for Dispatch")
	if delivery_order.status not in allowed_statuses:
		frappe.throw(_("Delivery Order {0} is not available for load planning.").format(delivery_order.name))

	defaults = {
		"company": first_order.company,
		"source_warehouse": first_order.source_warehouse,
		"delivery_method": canonical_delivery_method(first_order.delivery_method),
		"transport_zone": first_order.transport_zone,
		"dispatch_date": first_order.required_delivery_date,
	}
	for fieldname, value in defaults.items():
		if not load.get(fieldname) and value:
			load.set(fieldname, value)
	if load.source_warehouse and load.company:
		warehouse_company = frappe.db.get_value("Warehouse", load.source_warehouse, "company")
		if warehouse_company and warehouse_company != load.company:
			frappe.throw(
				_("Dispatch warehouse {0} belongs to company {1}, not {2}.").format(
					load.source_warehouse, warehouse_company, load.company
				)
			)

	comparisons = (
		("company", "company"),
		("transport_zone", "Transport Zone"),
	)
	for fieldname, label in comparisons:
		if (delivery_order.get(fieldname) or None) != (load.get(fieldname) or None):
			frappe.throw(
				_("Delivery Order {0} does not match the load's {1}.").format(delivery_order.name, label)
			)
	if not delivery_methods_are_compatible(delivery_order.delivery_method, load.delivery_method):
		frappe.throw(
			_("Delivery Order {0} does not match the load's Delivery Method.").format(delivery_order.name)
		)

	if (
		delivery_order.transport_compatibility_group
		and first_order.transport_compatibility_group
		and delivery_order.transport_compatibility_group != first_order.transport_compatibility_group
	):
		_raise_incompatible_transport_groups(first_order, delivery_order)

	canonical_method = canonical_delivery_method(delivery_order.delivery_method)
	if canonical_method and frappe.db.get_value("Delivery Method", canonical_method, "disabled"):
		frappe.throw(_("Delivery Method {0} is disabled.").format(canonical_method))

	load.refrigeration_required = bool(load.refrigeration_required or delivery_order.refrigeration_required)
	load.tail_lift_required = bool(load.tail_lift_required or delivery_order.tail_lift_required)
	if delivery_order.required_body_type:
		if load.required_body_type and load.required_body_type != delivery_order.required_body_type:
			frappe.throw(_("Delivery Orders in this load require incompatible vehicle body types."))
		load.required_body_type = delivery_order.required_body_type
	equipment = {
		value.strip()
		for text in (
			load.special_equipment_required,
			delivery_order.special_equipment_required,
		)
		for value in (text or "").replace("\n", ",").split(",")
		if value.strip()
	}
	load.special_equipment_required = ", ".join(sorted(equipment))


def _raise_incompatible_transport_groups(first_order, delivery_order):
	first_group = first_order.transport_compatibility_group or _("Not specified")
	current_group = delivery_order.transport_compatibility_group or _("Not specified")
	frappe.throw(
		_("Incompatible Transport Compatibility Groups: '{0}' and '{1}'.").format(first_group, current_group)
	)


def validate_transport_compatibility_groups(delivery_orders):
	"""Reject incompatible populated groups with an actionable error message."""
	first = next(
		(order for order in delivery_orders if order.transport_compatibility_group),
		delivery_orders[0] if delivery_orders else None,
	)
	if not first or not first.transport_compatibility_group:
		return
	for delivery_order in delivery_orders:
		if (
			delivery_order.transport_compatibility_group
			and delivery_order.transport_compatibility_group != first.transport_compatibility_group
		):
			_raise_incompatible_transport_groups(first, delivery_order)


def _populate_load_row(row, delivery_order, delivery_order_item, available_quantity):
	metrics = calculate_allocated_measurements(delivery_order_item, row.allocated_quantity)
	row.item_code = delivery_order_item.item_code
	row.item_name = delivery_order_item.item_name
	row.uom = delivery_order_item.uom
	row.stock_uom = delivery_order_item.stock_uom
	row.sales_order = delivery_order.sales_order
	row.customer = delivery_order.customer
	row.delivery_address = delivery_order.delivery_address
	row.delivery_group = getattr(delivery_order_item, "delivery_group", None) or getattr(
		delivery_order, "delivery_group", None
	)
	row.transport_compatibility_group = getattr(
		delivery_order_item, "transport_compatibility_group", None
	) or getattr(delivery_order, "transport_compatibility_group", None)
	row.available_quantity = available_quantity
	for fieldname, value in metrics.items():
		row.set(fieldname, value)


def calculate_allocated_measurements(delivery_order_item, allocated_quantity):
	"""Scale item-level transport dimensions to a partial load allocation."""
	transport_quantity = flt(delivery_order_item.transport_qty)
	allocated_quantity = flt(allocated_quantity)
	if transport_quantity <= 0:
		frappe.throw(_("Delivery Order Item {0} has no transport quantity.").format(delivery_order_item.name))
	if allocated_quantity > transport_quantity:
		frappe.throw(
			_("Allocated quantity cannot exceed transport quantity for Delivery Order Item {0}.").format(
				delivery_order_item.name
			)
		)

	ratio = allocated_quantity / transport_quantity
	return {
		"weight": flt(delivery_order_item.weight) * ratio,
		"weight_uom": delivery_order_item.weight_uom,
		"volume": flt(delivery_order_item.volume) * ratio,
		"volume_uom": delivery_order_item.volume_uom,
		"pallets": flt(delivery_order_item.pallets) * ratio,
		"cases": flt(delivery_order_item.cases) * ratio,
		"crates": flt(delivery_order_item.crates) * ratio,
	}


def get_available_quantity(delivery_order_name, delivery_order_item_name=None, exclude_load=None):
	"""Return quantity not reserved by an active draft or allocated on a submitted load."""
	if delivery_order_item_name:
		return get_available_quantities([delivery_order_item_name], exclude_load=exclude_load).get(
			delivery_order_item_name, 0
		)
	else:
		requested = flt(frappe.db.get_value("Delivery Order", delivery_order_name, "requested_quantity"))
	allocated = sum(
		flt(row.allocated_quantity)
		for row in _get_existing_allocations(delivery_order_name, delivery_order_item_name, exclude_load)
	)
	return max(requested - allocated, 0)


def get_available_quantities(delivery_order_item_names, exclude_load=None):
	"""Return live item balances using Sales Order delivery quantities and load commitments."""
	item_names = sorted(set(delivery_order_item_names or []))
	if not item_names:
		return {}
	item_rows = frappe.get_all(
		"Delivery Order Item",
		filters={"name": ["in", item_names]},
		fields=["name", "transport_qty", "sales_order_item"],
	)
	requested = {
		row.name: flt(row.transport_qty)
		for row in item_rows
	}
	sales_order_item_names = [row.sales_order_item for row in item_rows if row.sales_order_item]
	if sales_order_item_names:
		sales_order_quantities = {
			row.name: max(flt(row.qty) - flt(row.delivered_qty), 0)
			for row in frappe.get_all(
				"Sales Order Item",
				filters={"name": ["in", sales_order_item_names]},
				fields=["name", "qty", "delivered_qty"],
			)
		}
		for row in item_rows:
			if row.sales_order_item in sales_order_quantities:
				requested[row.name] = sales_order_quantities[row.sales_order_item]
	conditions = [
		"dlo.delivery_order_item in %(item_names)s",
		"dlo.parenttype = 'Delivery Load'",
		"dl.docstatus in (0, 1)",
	]
	values = {"item_names": tuple(item_names)}
	if exclude_load:
		conditions.append("dlo.parent != %(exclude_load)s")
		values["exclude_load"] = exclude_load
	allocated = dict(
		frappe.db.sql(
			f"""
				select dlo.delivery_order_item, coalesce(sum(dlo.allocated_quantity), 0)
				from `tabDelivery Load Order` dlo
				join `tabDelivery Load` dl on dl.name = dlo.parent
				where {" and ".join(conditions)}
				group by dlo.delivery_order_item
			""",
			values,
		)
	)
	return {
		item_name: max(flt(requested.get(item_name)) - flt(allocated.get(item_name)), 0)
		for item_name in item_names
	}


def _get_existing_allocations(delivery_order_name, delivery_order_item_name=None, exclude_load=None):
	conditions = [
		"dlo.delivery_order = %s",
		"dlo.parenttype = 'Delivery Load'",
		"dl.docstatus in (0, 1)",
	]
	values = [delivery_order_name]
	if delivery_order_item_name:
		conditions.append("dlo.delivery_order_item = %s")
		values.append(delivery_order_item_name)
	if exclude_load:
		conditions.append("dlo.parent != %s")
		values.append(exclude_load)

	return frappe.db.sql(
		f"""
			select dlo.allocated_quantity, dlo.parent, dl.docstatus
			from `tabDelivery Load Order` dlo
			join `tabDelivery Load` dl on dl.name = dlo.parent
			where {" and ".join(conditions)}
		""",
		tuple(values),
		as_dict=True,
	)


def sync_delivery_order_allocations(delivery_order_names=None):
	"""Synchronize submitted allocations, draft reservations, and live balance."""
	if delivery_order_names:
		delivery_order_names = sorted(set(delivery_order_names))
	else:
		delivery_order_names = frappe.get_all("Delivery Order", pluck="name")

	if not delivery_order_names:
		return

	item_rows = frappe.get_all(
		"Delivery Order Item",
		filters={"parent": ["in", delivery_order_names], "parenttype": "Delivery Order"},
		fields=["name", "parent", "transport_qty"],
	)
	items_by_order = defaultdict(list)
	for item in item_rows:
		items_by_order[item.parent].append(item)

	item_names = [item.name for item in item_rows]
	item_commitments = {}
	if item_names:
		commitment_rows = frappe.db.sql(
			"""
				select
					dlo.delivery_order_item,
					coalesce(sum(case when dl.docstatus = 0 then dlo.allocated_quantity else 0 end), 0) as reserved,
					coalesce(sum(case when dl.docstatus = 1 then dlo.allocated_quantity else 0 end), 0) as allocated
				from `tabDelivery Load Order` dlo
				join `tabDelivery Load` dl on dl.name = dlo.parent
				where dlo.delivery_order_item in %(item_names)s
				and dlo.parenttype = 'Delivery Load'
				and dl.docstatus in (0, 1)
				group by dlo.delivery_order_item
			""",
			{"item_names": tuple(item_names)},
			as_dict=True,
		)
		item_commitments = {row.delivery_order_item: row for row in commitment_rows}

	for delivery_order_name in delivery_order_names:
		quantities = frappe.db.sql(
			"""
				select
					coalesce(sum(case when dl.docstatus = 0 then dlo.allocated_quantity else 0 end), 0) as reserved,
					coalesce(sum(case when dl.docstatus = 1 then dlo.allocated_quantity else 0 end), 0) as allocated
				from `tabDelivery Load Order` dlo
				join `tabDelivery Load` dl on dl.name = dlo.parent
				where dlo.delivery_order = %s
				and dlo.parenttype = 'Delivery Load'
				and dl.docstatus in (0, 1)
			""",
			delivery_order_name,
			as_dict=True,
		)[0]
		requested, current_status = frappe.db.get_value(
			"Delivery Order",
			delivery_order_name,
			["requested_quantity", "status"],
		)
		requested = flt(requested)
		reserved = flt(quantities.reserved)
		allocated = flt(quantities.allocated)
		values = {
			"reserved_quantity": reserved,
			"allocated_quantity": allocated,
			"unallocated_quantity": max(requested - reserved - allocated, 0),
		}
		if current_status not in (
			"Dispatched",
			"Partially Delivered",
			"Delivered",
			"Cancelled",
		):
			values["status"] = (
				"Allocated"
				if allocated >= requested and requested
				else "Partially Allocated"
				if allocated
				else "Open"
			)
		frappe.db.set_value("Delivery Order", delivery_order_name, values, update_modified=False)

		for item in items_by_order.get(delivery_order_name, []):
			commitment = item_commitments.get(item.name, {})
			reserved = flt(commitment.get("reserved"))
			allocated = flt(commitment.get("allocated"))
			frappe.db.set_value(
				"Delivery Order Item",
				item.name,
				{
					"reserved_quantity": reserved,
					"allocated_quantity": allocated,
					"unallocated_quantity": max(flt(item.transport_qty) - reserved - allocated, 0),
				},
				update_modified=False,
			)
