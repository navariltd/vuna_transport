"""Services for transport load allocation and capacity planning."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from vuna_transport.services.demand.delivery_method_service import (
	canonical_delivery_method,
	delivery_methods_are_compatible,
)
from vuna_transport.services.fleet.vehicle_category_service import (
	capacity_override_enabled,
	configure_category_capacities,
	evaluate_vehicle_category,
	get_capacity_breakdown,
	get_vehicle_categories,
	validate_vehicle_access_limit,
)
from vuna_transport.services.planning.load_validation_service import (
	calculate_allocated_measurements,
	get_available_quantity,
	sync_delivery_order_allocations,
	validate_load_allocations,
	validate_transport_compatibility_groups,
)
from vuna_transport.services.shared.measurement_service import get_transport_uoms
from vuna_transport.services.shared.security_service import (
	require_company_access,
	require_doctype_permission,
	require_document_permission,
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
	from vuna_transport.services.demand.delivery_order_service import get_open_sales_order_names

	open_sales_orders = get_open_sales_order_names(filters.get("company"))
	if not open_sales_orders:
		return []
	filters["sales_order"] = ["in", open_sales_orders]

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
			"delivery_group",
			"transport_compatibility_group",
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
@frappe.validate_and_sanitize_search_inputs
def get_compatible_vehicle_categories(
	doctype: str, txt: str, searchfield: str, start: int, page_len: int, filters: dict | str | None, **kwargs
):
	"""Filter Vehicle Category choices using the current load requirements.

	This query improves the form experience only; Transport Load validation remains
	the final authority when the document is saved or submitted.
	"""
	require_doctype_permission("Vehicle Category", "read")
	filters = frappe.parse_json(filters) if isinstance(filters, str) else dict(filters or {})
	load_uoms = {
		"weight_uom": filters.get("weight_uom"),
		"volume_uom": filters.get("volume_uom"),
	}
	totals = {
		dimension: flt(filters.get(f"total_{dimension}"))
		for dimension in ("weight", "volume", "pallets", "cases", "crates")
	}
	requirements = {
		"refrigeration_required": bool(flt(filters.get("refrigeration_required"))),
		"tail_lift_required": bool(flt(filters.get("tail_lift_required"))),
		"required_body_type": filters.get("required_body_type"),
		"special_equipment_required": filters.get("special_equipment_required"),
	}
	delivery_order_names = filters.get("delivery_order_names") or []
	if isinstance(delivery_order_names, str):
		delivery_order_names = frappe.parse_json(delivery_order_names) or []
	demand_names = filters.get("transport_demand_names") or []
	if isinstance(demand_names, str):
		demand_names = frappe.parse_json(demand_names)
	if (
		not isinstance(demand_names, list)
		or len(demand_names) > 500
		or any(not isinstance(name, str) for name in demand_names)
	):
		frappe.throw(_("Select no more than 500 readable Transport Demands."))
	access_limits = []
	for name in sorted(set(demand_names)):
		demand = frappe.get_doc("Transport Demand", name)
		require_document_permission(demand, "read")
		access_limits.append(demand.maximum_vehicle_category)

	compatible = []
	for category in get_vehicle_categories(load_uoms, get_capacity_buffer_percentage()):
		result = evaluate_vehicle_category(totals, category, requirements)
		if not result["compatible"] or not _vehicle_meets_delivery_order_access_limits(
			category, delivery_order_names
		):
			continue
		if any(
			not validate_vehicle_access_limit(category, maximum)["compatible"] for maximum in access_limits
		):
			continue
		label = category.vehicle_category_name or category.name
		if txt and txt.lower() not in f"{category.name} {label}".lower():
			continue
		compatible.append((category.name, label))

	start = cint(start)
	page_len = min(cint(page_len) or 20, 50)
	return compatible[start : start + page_len]


@frappe.whitelist()
def get_vehicle_category_capacity_summary(vehicle_category, totals=None, weight_uom=None, volume_uom=None):
	"""Return the live capacity breakdown for a selected Vehicle Category."""
	require_doctype_permission("Vehicle Category", "read")
	totals = frappe.parse_json(totals) if isinstance(totals, str) else dict(totals or {})
	category = frappe.get_cached_doc("Vehicle Category", vehicle_category)
	configure_category_capacities(
		category,
		{"weight_uom": weight_uom, "volume_uom": volume_uom},
		get_capacity_buffer_percentage(),
		override_enabled=_capacity_override_enabled(),
	)
	return {
		"vehicle_category": category.name,
		"vehicle_category_label": category.vehicle_category_name or category.name,
		"capacity_override_percentage": flt(getattr(category, "capacity_override_percentage", 0)),
		"capacity_override_applied": category.capacity_override_applied,
		"capacity_override_allowed": category.capacity_override_allowed,
		"capacity": get_capacity_breakdown(totals, category),
	}


@frappe.whitelist()
def create_delivery_load(delivery_order_names: list[str] | str):
	"""Create a proposed load from one or more open Delivery Orders."""
	require_doctype_permission("Transport Load", "create")
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
def create_delivery_load_from_plan(
	allocations: list | str, vehicle_category: str | None = None, source_warehouse: str | None = None
):
	"""Create a draft load from planner-selected item quantities."""
	require_doctype_permission("Transport Load", "create")
	load = build_delivery_load_from_allocations(
		allocations, vehicle_category, source_warehouse=source_warehouse
	)
	load.insert()
	load.add_comment("Info", _("Created from the Dispatch Planning workbench."))
	return load.name


def build_delivery_load_from_allocations(allocations, vehicle_category=None, source_warehouse=None):
	"""Build one unsaved authoritative Transport Load from item allocations."""
	allocations = frappe.parse_json(allocations) if isinstance(allocations, str) else allocations
	if not isinstance(allocations, list) or not allocations:
		frappe.throw(_("Select at least one Delivery Order item."))
	if len(allocations) > MAX_PLANNING_ALLOCATION_ROWS:
		frappe.throw(
			_("A Transport Load cannot contain more than {0} planning rows.").format(
				MAX_PLANNING_ALLOCATION_ROWS
			)
		)
	if any(row.get("transport_demand") or row.get("transport_demand_item") for row in allocations):
		from vuna_transport.services.planning.customer_demand_load_service import _read_demand

		if any(
			not row.get("transport_demand")
			or not row.get("transport_demand_item")
			or row.get("delivery_order")
			or row.get("delivery_order_item")
			for row in allocations
		):
			frappe.throw(_("Select only customer Transport Demand items for this plan."))
		first = frappe.get_doc("Transport Demand", allocations[0]["transport_demand"])
		first.check_permission("read")
		if first.movement_type != "Customer Delivery":
			from vuna_transport.services.planning.transport_load_service import (
				build_transport_load_from_demands,
			)

			return build_transport_load_from_demands(allocations, vehicle_category, source_warehouse)
		first = _read_demand(first.name)
		load = frappe.new_doc("Transport Load")
		load.company, load.movement_type = first.company, "Customer Delivery"
		load.source_warehouse = source_warehouse or first.source_warehouse
		load.delivery_method = canonical_delivery_method(first.delivery_method)
		load.dispatch_date, load.transport_zone, load.transport_route = (
			first.required_date,
			first.transport_zone,
			first.preferred_route,
		)
		load.required_vehicle_category, load.status = vehicle_category, "Proposed"
		for row in allocations:
			load.append(
				"delivery_orders",
				{
					key: row.get(key)
					for key in ("transport_demand", "transport_demand_item", "allocated_quantity")
				},
			)
		return load

	delivery_order_names = sorted(
		{row.get("delivery_order") for row in allocations if row.get("delivery_order")}
	)
	if not delivery_order_names:
		frappe.throw(_("Every planning row must reference a Delivery Order."))
	delivery_orders = {name: frappe.get_doc("Delivery Order", name) for name in delivery_order_names}
	for delivery_order in delivery_orders.values():
		require_document_permission(delivery_order, "read")
	_validate_delivery_orders(list(delivery_orders.values()))
	first = delivery_orders[delivery_order_names[0]]
	require_company_access(first.company)

	load = frappe.new_doc("Transport Load")
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
			frappe.throw(_("Delivery Order Item {0} appears more than once.").format(item_name))
		seen_items.add(item_name)
		delivery_order = delivery_orders[delivery_order_name]
		stop_sequences.setdefault(delivery_order.delivery_address, len(stop_sequences) + 1)
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
def add_delivery_orders_to_load(load: str | Document, delivery_order_names: list[str] | str):
	"""Append all still-unallocated items from selected Delivery Orders to a draft load."""
	if isinstance(delivery_order_names, str):
		delivery_order_names = frappe.parse_json(delivery_order_names)
	if not delivery_order_names:
		frappe.throw(_("Select at least one Delivery Order."))

	load = frappe.get_doc("Transport Load", load) if isinstance(load, str) else load
	require_document_permission(load, "write")
	if load.docstatus != 0:
		frappe.throw(_("Items can only be fetched into a draft Transport Load."))

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
	_validate_dispatch_warehouse(load)
	if any(
		not delivery_methods_are_compatible(order.delivery_method, load.delivery_method)
		for order in delivery_orders
	):
		frappe.throw(_("All Delivery Orders in a load must use the same Delivery Method."))
	if any(order.transport_zone != load.transport_zone for order in delivery_orders):
		frappe.throw(_("All Delivery Orders in a load must use the same Transport Zone."))

	existing_items = {(row.delivery_order, row.delivery_order_item) for row in load.delivery_orders}
	bins = _get_on_hand_stock(
		[item.item_code for order in delivery_orders for item in order.items], load.source_warehouse
	)
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
			stock_required_quantity = available_quantity * flt(delivery_order_item.conversion_factor or 1)
			stock_available_quantity = flt(bins.get(delivery_order_item.item_code))
			load.append(
				"delivery_orders",
				{
					"delivery_order": delivery_order.name,
					"delivery_order_item": delivery_order_item.name,
					"item_code": delivery_order_item.item_code,
					"item_name": delivery_order_item.item_name,
					"uom": delivery_order_item.uom,
					"stock_uom": delivery_order_item.stock_uom,
					"sales_order": delivery_order.sales_order,
					"customer": delivery_order.customer,
					"delivery_address": delivery_order.delivery_address,
					"delivery_group": delivery_order_item.delivery_group or delivery_order.delivery_group,
					"transport_compatibility_group": (
						delivery_order_item.transport_compatibility_group
						or delivery_order.transport_compatibility_group
					),
					"allocated_quantity": available_quantity,
					"available_quantity": available_quantity,
					"stock_available_quantity": stock_available_quantity,
					"stock_required_quantity": stock_required_quantity,
					"stock_availability_status": (
						"Available" if stock_available_quantity >= stock_required_quantity else "Insufficient"
					),
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
def get_delivery_order_items(
	delivery_order_names: list[str] | str,
	delivery_order_item_names: list[str] | str | None = None,
	source_warehouse: str | None = None,
):
	"""Return validated, still-unallocated load rows for a client-side draft Load."""
	require_doctype_permission("Transport Load", "create")
	if isinstance(delivery_order_names, str):
		delivery_order_names = frappe.parse_json(delivery_order_names)
	if not delivery_order_names:
		frappe.throw(_("Select at least one Delivery Order."))
	if isinstance(delivery_order_item_names, str):
		delivery_order_item_names = frappe.parse_json(delivery_order_item_names)

	delivery_orders = [frappe.get_doc("Delivery Order", name) for name in delivery_order_names]
	for delivery_order in delivery_orders:
		require_document_permission(delivery_order, "read")
	require_company_access(delivery_orders[0].company)
	_validate_delivery_orders(delivery_orders)

	selected_item_names = set(delivery_order_item_names or [])
	valid_item_names = {item.name for order in delivery_orders for item in order.items}
	if selected_item_names and not selected_item_names.issubset(valid_item_names):
		frappe.throw(_("One or more selected items do not belong to the selected Delivery Orders."))

	planning_warehouse = source_warehouse or delivery_orders[0].source_warehouse
	bins = _get_on_hand_stock(
		[item.item_code for order in delivery_orders for item in order.items], planning_warehouse
	)
	items = []
	stop_sequences = {}
	for delivery_order in delivery_orders:
		for delivery_order_item in delivery_order.items:
			if selected_item_names and delivery_order_item.name not in selected_item_names:
				continue
			available_quantity = get_available_quantity(delivery_order.name, delivery_order_item.name)
			if available_quantity <= 0:
				continue

			stop_key = delivery_order.delivery_address
			stop_sequences.setdefault(stop_key, len(stop_sequences) + 1)
			ratio = available_quantity / flt(delivery_order_item.transport_qty or 1)
			stock_required_quantity = available_quantity * flt(delivery_order_item.conversion_factor or 1)
			stock_available_quantity = flt(bins.get(delivery_order_item.item_code))
			items.append(
				{
					"delivery_order": delivery_order.name,
					"delivery_order_item": delivery_order_item.name,
					"item_code": delivery_order_item.item_code,
					"item_name": delivery_order_item.item_name,
					"uom": delivery_order_item.uom,
					"stock_uom": delivery_order_item.stock_uom,
					"sales_order": delivery_order.sales_order,
					"customer": delivery_order.customer,
					"delivery_address": delivery_order.delivery_address,
					"delivery_group": delivery_order_item.delivery_group or delivery_order.delivery_group,
					"transport_compatibility_group": (
						delivery_order_item.transport_compatibility_group
						or delivery_order.transport_compatibility_group
					),
					"allocated_quantity": available_quantity,
					"available_quantity": available_quantity,
					"stock_available_quantity": stock_available_quantity,
					"stock_required_quantity": stock_required_quantity,
					"stock_availability_status": (
						"Available" if stock_available_quantity >= stock_required_quantity else "Insufficient"
					),
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
		"stock_warehouse": planning_warehouse,
		"items": items,
	}


def _get_on_hand_stock(item_codes, warehouse):
	"""Return on-hand stock by Item for planning visibility only.

	This intentionally does not reserve stock or decide Pick List/Delivery Note
	availability. ERPNext stock documents remain the execution authority.
	"""
	item_codes = sorted(set(filter(None, item_codes or [])))
	if not item_codes or not warehouse:
		return {}
	return {
		row.item_code: flt(row.actual_qty)
		for row in frappe.get_all(
			"Bin",
			filters={"warehouse": warehouse, "item_code": ["in", item_codes]},
			fields=["item_code", "actual_qty"],
		)
	}


@frappe.whitelist()
def get_delivery_order_item_allocation_preview(
	delivery_order_item, allocated_quantity=None, exclude_load=None, source_warehouse=None
):
	"""Return authoritative partial-allocation dimensions for immediate form feedback."""
	item = frappe.get_doc("Delivery Order Item", delivery_order_item)
	delivery_order = frappe.get_doc("Delivery Order", item.parent)
	require_document_permission(delivery_order, "read")
	available_quantity = get_available_quantity(delivery_order.name, item.name, exclude_load=exclude_load)
	quantity = available_quantity if allocated_quantity in (None, "") else flt(allocated_quantity)
	if quantity <= 0:
		frappe.throw(_("Allocated quantity must be greater than zero."))
	if quantity > available_quantity:
		frappe.throw(
			_("Allocated quantity exceeds the available quantity of {0}.").format(available_quantity)
		)
	planning_warehouse = source_warehouse or delivery_order.source_warehouse
	stock_available_quantity = flt(
		_get_on_hand_stock([item.item_code], planning_warehouse).get(item.item_code)
	)
	stock_required_quantity = quantity * flt(item.conversion_factor or 1)
	return {
		"delivery_order": delivery_order.name,
		"sales_order": delivery_order.sales_order,
		"customer": delivery_order.customer,
		"delivery_address": delivery_order.delivery_address,
		"allocated_quantity": quantity,
		"available_quantity": available_quantity,
		"item_code": item.item_code,
		"item_name": item.item_name,
		"uom": item.uom,
		"stock_uom": item.stock_uom,
		"stock_available_quantity": stock_available_quantity,
		"stock_required_quantity": stock_required_quantity,
		"stock_availability_status": (
			"Available" if stock_available_quantity >= stock_required_quantity else "Insufficient"
		),
		**calculate_allocated_measurements(item, quantity),
	}


def _validate_delivery_orders(delivery_orders):
	first = delivery_orders[0]
	for delivery_order in delivery_orders:
		if delivery_order.docstatus != 1:
			frappe.throw(
				_("Delivery Order {0} must be submitted before creating a Transport Load.").format(
					delivery_order.name
				)
			)
		if delivery_order.status not in ("Open", "Partially Allocated"):
			frappe.throw(
				_("Delivery Order {0} is not available for load planning.").format(delivery_order.name)
			)
		if delivery_order.company != first.company:
			frappe.throw(_("All Delivery Orders in a load must belong to the same company."))
		if not delivery_methods_are_compatible(delivery_order.delivery_method, first.delivery_method):
			frappe.throw(_("All Delivery Orders in a load must use the same Delivery Method."))
		if delivery_order.transport_zone != first.transport_zone:
			frappe.throw(_("All Delivery Orders in a load must use the same Transport Zone."))
		if (delivery_order.transport_handling_profile or "").strip() != (
			first.transport_handling_profile or ""
		).strip():
			frappe.throw(_("All Delivery Orders in a load must have compatible handling profiles."))
	validate_transport_compatibility_groups(delivery_orders)


def calculate_load_totals(load):
	"""Calculate aggregate capacity and stop values on a Transport Load."""
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


def _validate_dispatch_warehouse(load):
	"""Validate the selected dispatch warehouse independently of demand origin."""
	if not load.source_warehouse or not load.company:
		return
	warehouse_company = frappe.db.get_value("Warehouse", load.source_warehouse, "company")
	if warehouse_company and warehouse_company != load.company:
		frappe.throw(
			_("Dispatch warehouse {0} belongs to company {1}, not {2}.").format(
				load.source_warehouse, warehouse_company, load.company
			)
		)
	load.minimum_volume = load.total_volume


def validate_capacity(load, vehicle_category=None):
	"""Validate load capacity against a Vehicle Category, returning utilization details."""
	vehicle_category = vehicle_category or load.required_vehicle_category
	if not vehicle_category:
		return {"valid": True, "utilization": {}}

	vehicle = (
		frappe.get_doc("Vehicle Category", vehicle_category)
		if isinstance(vehicle_category, str)
		else vehicle_category
	)
	if vehicle.disabled:
		frappe.throw(_("Vehicle Category {0} is disabled.").format(vehicle.name))

	capacity_buffer = get_capacity_buffer_percentage()
	configure_category_capacities(
		vehicle,
		{"weight_uom": load.weight_uom, "volume_uom": load.volume_uom},
		capacity_buffer,
		override_enabled=_capacity_override_enabled(),
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
			_("Vehicle Category {0} is incompatible: {1}").format(vehicle.name, result["rejections"][0])
		)
	_validate_load_vehicle_access_limits(load, vehicle)

	return {
		"valid": True,
		"utilization": result["utilization"],
		"capacity_buffer_percentage": capacity_buffer,
		"capacity_override_percentage": flt(getattr(vehicle, "capacity_override_percentage", 0)),
		"capacity_override_applied": vehicle.capacity_override_applied,
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


def _load_access_limits(load):
	sources = set()
	for row in load.delivery_orders:
		if getattr(row, "delivery_order", None):
			sources.add(("Delivery Order", row.delivery_order))
		elif getattr(row, "transport_demand", None):
			sources.add(("Transport Demand", row.transport_demand))
	return [
		(name, frappe.db.get_value(doctype, name, "maximum_vehicle_category"))
		for doctype, name in sorted(sources)
	]


def _validate_load_vehicle_access_limits(load, vehicle):
	for delivery_order, maximum_category in _load_access_limits(load):
		result = validate_vehicle_access_limit(vehicle, maximum_category)
		if not result["compatible"]:
			frappe.throw(
				_("Vehicle Category {0} is incompatible with delivery location {1}: {2}").format(
					vehicle.name, delivery_order, result["reason"]
				)
			)


def _vehicle_meets_load_access_limits(load, vehicle):
	for _source, maximum_category in _load_access_limits(load):
		if not validate_vehicle_access_limit(vehicle, maximum_category)["compatible"]:
			return False
	return True


def _vehicle_meets_delivery_order_access_limits(vehicle, delivery_order_names):
	"""Apply each selected Delivery Order's maximum vehicle restriction."""
	for delivery_order in set(delivery_order_names or []):
		maximum_category = frappe.db.get_value("Delivery Order", delivery_order, "maximum_vehicle_category")
		if not validate_vehicle_access_limit(vehicle, maximum_category)["compatible"]:
			return False
	return True


def _fits(required, capacity):
	return not flt(capacity) or flt(required) <= flt(capacity)


def get_capacity_buffer_percentage():
	"""Return the legacy global safety buffer, retained for API compatibility."""
	return 0


def _capacity_override_enabled():
	"""Return whether the current user may use a category's overload allowance."""
	return capacity_override_enabled()


def apply_capacity_buffer(capacity, buffer_percentage):
	"""Return usable capacity while preserving zero as an unconfigured dimension."""
	capacity = flt(capacity)
	if not capacity:
		return 0
	return capacity * (1 - flt(buffer_percentage) / 100)
