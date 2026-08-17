"""Authoritative Delivery Load compatibility, reservation, and quantity rules."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, getdate


PLANNABLE_DELIVERY_ORDER_STATUSES = ("Open", "Partially Allocated")


def validate_load_allocations(load, lock=True):
	"""Validate and rebuild every load row from authoritative Delivery Order data."""
	if not load.delivery_orders:
		frappe.throw(_("At least one Delivery Order is required."))

	item_names = sorted({row.delivery_order_item for row in load.delivery_orders if row.delivery_order_item})
	if lock:
		_lock_delivery_order_items(item_names)

	delivery_orders = {}
	delivery_order_items = {}
	current_allocations = defaultdict(float)
	first_order = None

	for row in load.delivery_orders:
		_validate_required_row_links(row)
		delivery_order = delivery_orders.get(row.delivery_order)
		if not delivery_order:
			delivery_order = frappe.get_doc("Delivery Order", row.delivery_order)
			delivery_orders[row.delivery_order] = delivery_order
		if first_order is None:
			first_order = delivery_order
		_validate_delivery_order_for_load(load, delivery_order, first_order)

		delivery_order_item = delivery_order_items.get(row.delivery_order_item)
		if not delivery_order_item:
			delivery_order_item = frappe.get_doc(
				"Delivery Order Item", row.delivery_order_item
			)
			delivery_order_items[row.delivery_order_item] = delivery_order_item
		if delivery_order_item.parent != delivery_order.name:
			frappe.throw(
				_("Delivery Order Item {0} does not belong to Delivery Order {1}.").format(
					delivery_order_item.name, delivery_order.name
				)
			)

		key = (delivery_order.name, delivery_order_item.name)
		current_allocations[key] += flt(row.allocated_quantity)
		_populate_load_row(row, delivery_order, delivery_order_item)

	for (delivery_order_name, delivery_order_item_name), current_quantity in current_allocations.items():
		available_quantity = get_available_quantity(
			delivery_order_name,
			delivery_order_item_name,
			exclude_load=load.name,
		)
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


def _validate_delivery_order_for_load(load, delivery_order, first_order):
	if delivery_order.docstatus != 1:
		frappe.throw(
			_("Delivery Order {0} must be submitted before it can be loaded.").format(
				delivery_order.name
			)
		)
	if delivery_order.status not in PLANNABLE_DELIVERY_ORDER_STATUSES:
		frappe.throw(
			_("Delivery Order {0} is not available for load planning.").format(delivery_order.name)
		)

	defaults = {
		"company": first_order.company,
		"source_warehouse": first_order.source_warehouse,
		"delivery_method": first_order.delivery_method,
		"transport_zone": first_order.transport_zone,
		"dispatch_date": first_order.required_delivery_date,
	}
	for fieldname, value in defaults.items():
		if not load.get(fieldname) and value:
			load.set(fieldname, value)

	comparisons = (
		("company", "company"),
		("source_warehouse", "source warehouse"),
		("delivery_method", "Delivery Method"),
		("transport_zone", "Transport Zone"),
	)
	for fieldname, label in comparisons:
		if (delivery_order.get(fieldname) or None) != (load.get(fieldname) or None):
			frappe.throw(
				_("Delivery Order {0} does not match the load's {1}.").format(
					delivery_order.name, label
				)
			)

	if getdate(delivery_order.required_delivery_date) != getdate(first_order.required_delivery_date):
		frappe.throw(_("All Delivery Orders in a load must have the same required delivery date."))
	if (delivery_order.transport_handling_profile or "").strip() != (
		first_order.transport_handling_profile or ""
	).strip():
		frappe.throw(_("All Delivery Orders in a load must have compatible handling profiles."))

	if delivery_order.delivery_method and frappe.db.get_value(
		"Delivery Method", delivery_order.delivery_method, "disabled"
	):
		frappe.throw(
			_("Delivery Method {0} is disabled.").format(delivery_order.delivery_method)
		)

	load.refrigeration_required = bool(
		load.refrigeration_required or delivery_order.refrigeration_required
	)
	load.tail_lift_required = bool(
		load.tail_lift_required or delivery_order.tail_lift_required
	)
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


def _populate_load_row(row, delivery_order, delivery_order_item):
	metrics = calculate_allocated_measurements(delivery_order_item, row.allocated_quantity)
	row.sales_order = delivery_order.sales_order
	row.customer = delivery_order.customer
	row.delivery_address = delivery_order.delivery_address
	row.available_quantity = get_available_quantity(
		delivery_order.name,
		delivery_order_item.name,
		exclude_load=row.parent,
	)
	for fieldname, value in metrics.items():
		row.set(fieldname, value)


def calculate_allocated_measurements(delivery_order_item, allocated_quantity):
	"""Scale item-level transport dimensions to a partial load allocation."""
	transport_quantity = flt(delivery_order_item.transport_qty)
	allocated_quantity = flt(allocated_quantity)
	if transport_quantity <= 0:
		frappe.throw(
			_("Delivery Order Item {0} has no transport quantity.").format(
				delivery_order_item.name
			)
		)
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
		requested = flt(
			frappe.db.get_value("Delivery Order Item", delivery_order_item_name, "transport_qty")
		)
	else:
		requested = flt(
			frappe.db.get_value("Delivery Order", delivery_order_name, "requested_quantity")
		)
	allocated = sum(
		flt(row.allocated_quantity)
		for row in _get_existing_allocations(
			delivery_order_name, delivery_order_item_name, exclude_load
		)
	)
	return max(requested - allocated, 0)


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
			where {' and '.join(conditions)}
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
			"Delivery Exception",
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
		frappe.db.set_value(
			"Delivery Order", delivery_order_name, values, update_modified=False
		)
