"""Services for transport load allocation and capacity planning."""

import frappe
from frappe import _
from frappe.utils import flt

from vuna_transport.services.demand.delivery_method_service import (
	canonical_delivery_method,
	delivery_methods_are_compatible,
)
from vuna_transport.services.shared.measurement_service import get_transport_uoms
from vuna_transport.services.planning.load_validation_service import (
	calculate_allocated_measurements,
	get_available_quantity,
	sync_delivery_order_allocations,
	validate_load_allocations,
)
from vuna_transport.services.shared.security_service import (
	require_company_access,
	require_doctype_permission,
	require_document_permission,
)
from vuna_transport.services.fleet.vehicle_category_service import (
	evaluate_vehicle_category,
	get_effective_capacities,
	get_vehicle_categories,
	validate_vehicle_access_limit,
)


MAX_PLANNING_ALLOCATION_ROWS = 500


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_available_delivery_orders(
	doctype, txt, searchfield, start, page_len, filters, as_dict=False, **kwargs
):
	"""Return Delivery Orders with their live unallocated quantities."""
	require_doctype_permission("Delivery Order", "read")
	filters = frappe.parse_json(filters) if isinstance(filters, str) else dict(filters or {})
	filters.setdefault("docstatus", 1)
	filters.setdefault("status", ["in", ["Open", "Partially Allocated"]])

	delivery_orders = frappe.get_list(
		"Delivery Order",
		filters=filters,
		or_filters={
			"name": ["like", f"%{txt}%"],
			"customer": ["like", f"%{txt}%"],
			"customer_name": ["like", f"%{txt}%"],
			"sales_order": ["like", f"%{txt}%"],
		},
		fields=[
			"name",
			"customer_name",
			"required_delivery_date",
			"requested_quantity",
			"reserved_quantity",
			"allocated_quantity",
		],
		order_by="required_delivery_date asc, priority desc, creation asc",
		limit_start=start,
		limit_page_length=page_len,
	)

	available_delivery_orders = []
	for delivery_order in delivery_orders:
		delivery_order.unallocated_quantity = get_available_quantity(delivery_order.name)
		if delivery_order.unallocated_quantity > 0:
			available_delivery_orders.append(delivery_order)

	if as_dict:
		return available_delivery_orders
	return [tuple(delivery_order.values()) for delivery_order in available_delivery_orders]


@frappe.whitelist()
def create_delivery_load(delivery_order_names):
	"""Create a proposed load from one or more open Delivery Orders."""
	require_doctype_permission("Delivery Load", "create")
	if isinstance(delivery_order_names, str):
		delivery_order_names = frappe.parse_json(delivery_order_names)

	if not delivery_order_names:
		frappe.throw(_("Select at least one Delivery Order."))

	delivery_orders = [frappe.get_doc("Delivery Order", name) for name in delivery_order_names]
	for delivery_order in delivery_orders:
		require_document_permission(delivery_order, "read")
	require_company_access(delivery_orders[0].company)
	_validate_delivery_orders(delivery_orders)

	allocations = []
	for delivery_order in delivery_orders:
		for delivery_order_item in delivery_order.items:
			available_quantity = get_available_quantity(delivery_order.name, delivery_order_item.name)
			if available_quantity <= 0:
				continue
			allocations.append(
				{
					"delivery_order": delivery_order.name,
					"delivery_order_item": delivery_order_item.name,
					"allocated_quantity": available_quantity,
				}
			)
	if not allocations:
		frappe.throw(_("The selected Delivery Orders have no unallocated quantity."))
	load = build_delivery_load_from_allocations(allocations)
	load.insert()
	return load.name


@frappe.whitelist()
def create_delivery_load_from_plan(allocations, vehicle_category=None):
	"""Create a draft load from planner-selected item quantities."""
	require_doctype_permission("Delivery Load", "create")
	load = build_delivery_load_from_allocations(allocations, vehicle_category)
	load.insert()
	load.add_comment("Info", _("Created from the Dispatch Planning workbench."))
	return load.name


def build_delivery_load_from_allocations(allocations, vehicle_category=None):
	"""Build one unsaved authoritative Delivery Load from item allocations."""
	allocations = frappe.parse_json(allocations) if isinstance(allocations, str) else allocations
	if not isinstance(allocations, list) or not allocations:
		frappe.throw(_("Select at least one Delivery Order item."))
	if len(allocations) > MAX_PLANNING_ALLOCATION_ROWS:
		frappe.throw(
			_("A Delivery Load cannot contain more than {0} planning rows.").format(
				MAX_PLANNING_ALLOCATION_ROWS
			)
		)

	delivery_order_names = sorted(
		{row.get("delivery_order") for row in allocations if row.get("delivery_order")}
	)
	if not delivery_order_names:
		frappe.throw(_("Every planning row must reference a Delivery Order."))
	delivery_orders = {
		name: frappe.get_doc("Delivery Order", name) for name in delivery_order_names
	}
	for delivery_order in delivery_orders.values():
		require_document_permission(delivery_order, "read")
	_validate_delivery_orders(list(delivery_orders.values()))
	first = delivery_orders[delivery_order_names[0]]
	require_company_access(first.company)

	load = frappe.new_doc("Delivery Load")
	load.company = first.company
	load.source_warehouse = first.source_warehouse
	load.delivery_method = canonical_delivery_method(first.delivery_method)
	load.dispatch_date = first.required_delivery_date
	load.transport_zone = first.transport_zone
	load.transport_route = first.preferred_route
	load.required_vehicle_category = vehicle_category
	load.status = "Proposed"

	stop_sequences = {}
	seen_items = set()
	for row in allocations:
		delivery_order_name = row.get("delivery_order")
		item_name = row.get("delivery_order_item")
		quantity = flt(row.get("allocated_quantity"))
		if delivery_order_name not in delivery_orders or not item_name or quantity <= 0:
			frappe.throw(_("Planning allocations are incomplete or invalid."))
		if item_name in seen_items:
			frappe.throw(
				_("Delivery Order Item {0} appears more than once.").format(item_name)
			)
		seen_items.add(item_name)
		delivery_order = delivery_orders[delivery_order_name]
		stop_sequences.setdefault(
			delivery_order.delivery_address, len(stop_sequences) + 1
		)
		load.append(
			"delivery_orders",
			{
				"delivery_order": delivery_order_name,
				"delivery_order_item": item_name,
				"allocated_quantity": quantity,
				"stop_sequence": stop_sequences[delivery_order.delivery_address],
			},
		)
	return load


@frappe.whitelist()
def add_delivery_orders_to_load(load, delivery_order_names):
	"""Append all still-unallocated items from selected Delivery Orders to a draft load."""
	if isinstance(delivery_order_names, str):
		delivery_order_names = frappe.parse_json(delivery_order_names)
	if not delivery_order_names:
		frappe.throw(_("Select at least one Delivery Order."))

	load = frappe.get_doc("Delivery Load", load) if isinstance(load, str) else load
	require_document_permission(load, "write")
	if load.docstatus != 0:
		frappe.throw(_("Items can only be fetched into a draft Delivery Load."))

	delivery_orders = [frappe.get_doc("Delivery Order", name) for name in delivery_order_names]
	for delivery_order in delivery_orders:
		require_document_permission(delivery_order, "read")
	_validate_delivery_orders(delivery_orders)

	if not load.company:
		load.company = delivery_orders[0].company
	if not load.source_warehouse:
		load.source_warehouse = delivery_orders[0].source_warehouse
	if not load.delivery_method:
		load.delivery_method = canonical_delivery_method(delivery_orders[0].delivery_method)
	if not load.transport_zone:
		load.transport_zone = delivery_orders[0].transport_zone
	if not load.dispatch_date:
		load.dispatch_date = min(order.required_delivery_date for order in delivery_orders)

	if any(order.company != load.company for order in delivery_orders):
		frappe.throw(_("All Delivery Orders in a load must belong to the same company."))
	if any(order.source_warehouse != load.source_warehouse for order in delivery_orders):
		frappe.throw(_("All Delivery Orders in a load must use the same source warehouse."))
	if any(
		not delivery_methods_are_compatible(order.delivery_method, load.delivery_method)
		for order in delivery_orders
	):
		frappe.throw(_("All Delivery Orders in a load must use the same Delivery Method."))
	if any(order.transport_zone != load.transport_zone for order in delivery_orders):
		frappe.throw(_("All Delivery Orders in a load must use the same Transport Zone."))

	existing_items = {
		(row.delivery_order, row.delivery_order_item)
		for row in load.delivery_orders
	}
	stop_sequences = {
		row.delivery_address: row.stop_sequence
		for row in load.delivery_orders
		if row.delivery_address and row.stop_sequence
	}
	appended_count = 0

	for delivery_order in delivery_orders:
		for delivery_order_item in delivery_order.items:
			key = (delivery_order.name, delivery_order_item.name)
			if key in existing_items:
				continue

			available_quantity = get_available_quantity(delivery_order.name, delivery_order_item.name)
			if available_quantity <= 0:
				continue

			stop_key = delivery_order.delivery_address
			stop_sequences.setdefault(stop_key, len(stop_sequences) + 1)
			ratio = available_quantity / flt(delivery_order_item.transport_qty or 1)
			load.append(
				"delivery_orders",
				{
					"delivery_order": delivery_order.name,
					"delivery_order_item": delivery_order_item.name,
					"sales_order": delivery_order.sales_order,
					"customer": delivery_order.customer,
					"delivery_address": delivery_order.delivery_address,
					"allocated_quantity": available_quantity,
					"available_quantity": available_quantity,
					"weight": flt(delivery_order_item.weight) * ratio,
					"weight_uom": delivery_order_item.weight_uom,
					"volume": flt(delivery_order_item.volume) * ratio,
					"volume_uom": delivery_order_item.volume_uom,
					"pallets": flt(delivery_order_item.pallets) * ratio,
					"cases": flt(delivery_order_item.cases) * ratio,
					"crates": flt(delivery_order_item.crates) * ratio,
					"stop_sequence": stop_sequences[stop_key],
				},
			)
			appended_count += 1

	if not appended_count:
		frappe.throw(_("The selected Delivery Orders have no unallocated quantity."))

	load.save()
	return load.name


@frappe.whitelist()
def get_delivery_order_items(delivery_order_names):
	"""Return validated, still-unallocated load rows for a client-side draft Load."""
	require_doctype_permission("Delivery Load", "create")
	if isinstance(delivery_order_names, str):
		delivery_order_names = frappe.parse_json(delivery_order_names)
	if not delivery_order_names:
		frappe.throw(_("Select at least one Delivery Order."))

	delivery_orders = [frappe.get_doc("Delivery Order", name) for name in delivery_order_names]
	for delivery_order in delivery_orders:
		require_document_permission(delivery_order, "read")
	require_company_access(delivery_orders[0].company)
	_validate_delivery_orders(delivery_orders)

	items = []
	stop_sequences = {}
	for delivery_order in delivery_orders:
		for delivery_order_item in delivery_order.items:
			available_quantity = get_available_quantity(delivery_order.name, delivery_order_item.name)
			if available_quantity <= 0:
				continue

			stop_key = delivery_order.delivery_address
			stop_sequences.setdefault(stop_key, len(stop_sequences) + 1)
			ratio = available_quantity / flt(delivery_order_item.transport_qty or 1)
			items.append(
				{
					"delivery_order": delivery_order.name,
					"delivery_order_item": delivery_order_item.name,
					"sales_order": delivery_order.sales_order,
					"customer": delivery_order.customer,
					"delivery_address": delivery_order.delivery_address,
					"allocated_quantity": available_quantity,
					"available_quantity": available_quantity,
					"weight": flt(delivery_order_item.weight) * ratio,
					"weight_uom": delivery_order_item.weight_uom,
					"volume": flt(delivery_order_item.volume) * ratio,
					"volume_uom": delivery_order_item.volume_uom,
					"pallets": flt(delivery_order_item.pallets) * ratio,
					"cases": flt(delivery_order_item.cases) * ratio,
					"crates": flt(delivery_order_item.crates) * ratio,
					"stop_sequence": stop_sequences[stop_key],
				}
			)

	if not items:
		frappe.throw(_("The selected Delivery Orders have no unallocated quantity."))

	return {
		"company": delivery_orders[0].company,
		"source_warehouse": delivery_orders[0].source_warehouse,
		"delivery_method": canonical_delivery_method(delivery_orders[0].delivery_method),
		"transport_zone": delivery_orders[0].transport_zone,
		"transport_route": delivery_orders[0].preferred_route,
		"dispatch_date": min(order.required_delivery_date for order in delivery_orders),
		"weight_uom": delivery_orders[0].weight_uom,
		"volume_uom": delivery_orders[0].volume_uom,
		"items": items,
	}


@frappe.whitelist()
def get_delivery_order_item_allocation_preview(
	delivery_order_item, allocated_quantity=None, exclude_load=None
):
	"""Return authoritative partial-allocation dimensions for immediate form feedback."""
	item = frappe.get_doc("Delivery Order Item", delivery_order_item)
	delivery_order = frappe.get_doc("Delivery Order", item.parent)
	require_document_permission(delivery_order, "read")
	available_quantity = get_available_quantity(
		delivery_order.name, item.name, exclude_load=exclude_load
	)
	quantity = available_quantity if allocated_quantity in (None, "") else flt(allocated_quantity)
	if quantity <= 0:
		frappe.throw(_("Allocated quantity must be greater than zero."))
	if quantity > available_quantity:
		frappe.throw(
			_("Allocated quantity exceeds the available quantity of {0}.").format(
				available_quantity
			)
		)
	return {
		"delivery_order": delivery_order.name,
		"sales_order": delivery_order.sales_order,
		"customer": delivery_order.customer,
		"delivery_address": delivery_order.delivery_address,
		"allocated_quantity": quantity,
		"available_quantity": available_quantity,
		**calculate_allocated_measurements(item, quantity),
	}


def _validate_delivery_orders(delivery_orders):
	first = delivery_orders[0]
	for delivery_order in delivery_orders:
		if delivery_order.docstatus != 1:
			frappe.throw(
				_("Delivery Order {0} must be submitted before creating a Delivery Load.").format(
					delivery_order.name
				)
			)
		if delivery_order.status not in ("Open", "Partially Allocated"):
			frappe.throw(_("Delivery Order {0} is not available for load planning.").format(delivery_order.name))
		if delivery_order.company != first.company:
			frappe.throw(_("All Delivery Orders in a load must belong to the same company."))
		if delivery_order.source_warehouse != first.source_warehouse:
			frappe.throw(_("All Delivery Orders in a load must use the same source warehouse."))
		if not delivery_methods_are_compatible(
			delivery_order.delivery_method, first.delivery_method
		):
			frappe.throw(_("All Delivery Orders in a load must use the same Delivery Method."))
		if delivery_order.transport_zone != first.transport_zone:
			frappe.throw(_("All Delivery Orders in a load must use the same Transport Zone."))
		if delivery_order.required_delivery_date != first.required_delivery_date:
			frappe.throw(_("All Delivery Orders in a load must have the same required delivery date."))
		if (delivery_order.transport_handling_profile or "").strip() != (
			first.transport_handling_profile or ""
		).strip():
			frappe.throw(_("All Delivery Orders in a load must have compatible handling profiles."))


def calculate_load_totals(load):
	"""Calculate aggregate capacity and stop values on a Delivery Load."""
	uoms = get_transport_uoms()
	load.weight_uom = uoms.weight_uom or next(
		(
			getattr(row, "weight_uom", None)
			for row in load.delivery_orders
			if getattr(row, "weight_uom", None)
		),
		None,
	)
	load.volume_uom = uoms.volume_uom or next(
		(
			getattr(row, "volume_uom", None)
			for row in load.delivery_orders
			if getattr(row, "volume_uom", None)
		),
		None,
	)
	load.total_weight = sum(flt(row.weight) for row in load.delivery_orders)
	load.total_volume = sum(flt(row.volume) for row in load.delivery_orders)
	load.total_pallets = sum(flt(row.pallets) for row in load.delivery_orders)
	load.total_cases = sum(flt(row.cases) for row in load.delivery_orders)
	load.total_crates = sum(flt(row.crates) for row in load.delivery_orders)
	load.total_stops = len({row.delivery_address for row in load.delivery_orders if row.delivery_address})
	load.minimum_payload = load.total_weight
	load.minimum_volume = load.total_volume


def validate_capacity(load, vehicle_category=None):
	"""Validate load capacity against a Vehicle Category, returning utilization details."""
	vehicle_category = vehicle_category or load.required_vehicle_category
	if not vehicle_category:
		return {"valid": True, "utilization": {}}

	vehicle = frappe.get_doc("Vehicle Category", vehicle_category) if isinstance(vehicle_category, str) else vehicle_category
	if vehicle.disabled:
		frappe.throw(_("Vehicle Category {0} is disabled.").format(vehicle.name))

	capacity_buffer = get_capacity_buffer_percentage()
	vehicle.capacities = get_effective_capacities(
		vehicle,
		{"weight_uom": load.weight_uom, "volume_uom": load.volume_uom},
		capacity_buffer,
	)
	result = evaluate_vehicle_category(
		{
			"weight": load.total_weight,
			"volume": load.total_volume,
			"pallets": load.total_pallets,
			"cases": load.total_cases,
			"crates": load.total_crates,
		},
		vehicle,
		{
			"refrigeration_required": load.refrigeration_required,
			"tail_lift_required": load.tail_lift_required,
			"required_body_type": load.required_body_type,
			"special_equipment_required": load.special_equipment_required,
		},
	)
	if not result["compatible"]:
		frappe.throw(
			_("Vehicle Category {0} is incompatible: {1}").format(
				vehicle.name, result["rejections"][0]
			)
		)
	_validate_load_vehicle_access_limits(load, vehicle)

	return {
		"valid": True,
		"utilization": result["utilization"],
		"capacity_buffer_percentage": capacity_buffer,
	}


def recommend_vehicle_category(load):
	"""Return the smallest active Vehicle Category that satisfies load constraints."""
	capacity_buffer = get_capacity_buffer_percentage()
	vehicle_categories = get_vehicle_categories(
		{"weight_uom": load.weight_uom, "volume_uom": load.volume_uom},
		capacity_buffer,
	)
	compatible = []
	for vehicle in vehicle_categories:
		result = evaluate_vehicle_category(
			{
				"weight": load.total_weight,
				"volume": load.total_volume,
				"pallets": load.total_pallets,
				"cases": load.total_cases,
				"crates": load.total_crates,
			},
			vehicle,
			{
				"refrigeration_required": load.refrigeration_required,
				"tail_lift_required": load.tail_lift_required,
				"required_body_type": load.required_body_type,
				"special_equipment_required": load.special_equipment_required,
			},
		)
		if result["compatible"] and _vehicle_meets_load_access_limits(load, vehicle):
			compatible.append(
				(
					vehicle.capacities["weight"] or float("inf"),
					vehicle.capacities["volume"] or float("inf"),
					vehicle.name,
				)
			)

	if compatible:
		return min(compatible)[2]

	frappe.throw(_("No active Vehicle Category can satisfy the current load capacity requirements."))


def get_compatible_vehicle_category_names(load):
	"""Return every category that can execute the load, including larger substitutes."""
	categories = get_vehicle_categories(
		{"weight_uom": load.weight_uom, "volume_uom": load.volume_uom},
		get_capacity_buffer_percentage(),
	)
	compatible = []
	for category in categories:
		result = evaluate_vehicle_category(
			{
				"weight": load.total_weight,
				"volume": load.total_volume,
				"pallets": load.total_pallets,
				"cases": load.total_cases,
				"crates": load.total_crates,
			},
			category,
			{
				"refrigeration_required": load.refrigeration_required,
				"tail_lift_required": load.tail_lift_required,
				"required_body_type": load.required_body_type,
				"special_equipment_required": load.special_equipment_required,
			},
		)
		if result["compatible"] and _vehicle_meets_load_access_limits(load, category):
			compatible.append(category.name)
	return compatible


def _validate_load_vehicle_access_limits(load, vehicle):
	for delivery_order in {
		row.delivery_order for row in load.delivery_orders if row.delivery_order
	}:
		maximum_category = frappe.db.get_value(
			"Delivery Order", delivery_order, "maximum_vehicle_category"
		)
		result = validate_vehicle_access_limit(vehicle, maximum_category)
		if not result["compatible"]:
			frappe.throw(
				_("Vehicle Category {0} is incompatible with Delivery Order {1}: {2}").format(
					vehicle.name, delivery_order, result["reason"]
				)
			)


def _vehicle_meets_load_access_limits(load, vehicle):
	for delivery_order in {
		row.delivery_order for row in load.delivery_orders if row.delivery_order
	}:
		maximum_category = frappe.db.get_value(
			"Delivery Order", delivery_order, "maximum_vehicle_category"
		)
		if not validate_vehicle_access_limit(vehicle, maximum_category)["compatible"]:
			return False
	return True


def _fits(required, capacity):
	return not flt(capacity) or flt(required) <= flt(capacity)


def get_capacity_buffer_percentage():
	return flt(
		frappe.get_cached_doc("Transport Settings").default_capacity_buffer_percentage
	)


def apply_capacity_buffer(capacity, buffer_percentage):
	"""Return usable capacity while preserving zero as an unconfigured dimension."""
	capacity = flt(capacity)
	if not capacity:
		return 0
	return capacity * (1 - flt(buffer_percentage) / 100)
