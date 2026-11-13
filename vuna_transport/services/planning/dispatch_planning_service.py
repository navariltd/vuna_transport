"""Bounded, permission-aware APIs for the Dispatch Planning Desk page."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from vuna_transport.services.costing.transport_cost_estimation_service import (
	estimate_transport_cost,
	estimate_transport_cost_for_cost_structure,
)
from vuna_transport.services.fleet.driver_availability_service import (
	get_driver_availability_map,
)
from vuna_transport.services.fleet.vehicle_allocation_service import get_load_resource_window
from vuna_transport.services.fleet.vehicle_availability_service import (
	get_vehicle_availability_map,
)
from vuna_transport.services.fleet.vehicle_category_service import (
	evaluate_vehicle_category,
	get_capacity_breakdown,
	get_effective_capacities,
	get_vehicle_categories,
	validate_vehicle_access_limit,
)
from vuna_transport.services.planning import customer_planning_source as source
from vuna_transport.services.planning.delivery_load_service import (
	build_delivery_load_from_allocations,
	calculate_load_totals,
	get_capacity_buffer_percentage,
	get_compatible_vehicle_category_names,
	recommend_vehicle_category,
)
from vuna_transport.services.planning.delivery_load_service import (
	create_delivery_load_from_plan as _create_delivery_load_from_plan,
)
from vuna_transport.services.planning.dispatch_filter_service import (
	as_filter_conditions,
	get_filter_definitions,
	validate_additional_filters,
	validate_configured_filters,
)
from vuna_transport.services.planning.load_validation_service import validate_load_allocations
from vuna_transport.services.shared.security_service import (
	require_company_access,
	require_doctype_permission,
	require_document_permission,
)

MAX_PAGE_LENGTH = 100
MAX_SUMMARY_ORDERS = 500
MAX_DETAIL_ORDERS = 50
MAX_SELECTED_ORDERS = 100
PLANNABLE_STATUSES = ("Open", "Partially Allocated")
CAPACITY_DIMENSIONS = ("weight", "volume", "pallets", "cases", "crates")


@frappe.whitelist()
def get_customer_planning_context() -> dict:
	"""Use server-selected DocType metadata and canonical allocation fields."""
	require_doctype_permission("Transport Load", "read")
	parent, item = source.allocation_fields()
	return {
		"doctype": source.doctype(),
		"order_field": parent,
		"item_field": item,
		"costing_enabled": bool(frappe.db.get_single_value("Transport Settings", "enable_transport_costing")),
	}


@frappe.whitelist()
def get_planning_demand(
	company: str,
	required_delivery_date_from: str | None = None,
	required_delivery_date_to: str | None = None,
	source_warehouse: str | None = None,
	transport_zone: str | None = None,
	delivery_method: str | None = None,
	priority: str | None = None,
	customer: str | None = None,
	delivery_address: str | None = None,
	territory: str | None = None,
	county: str | None = None,
	town: str | None = None,
	constituency: str | None = None,
	allocation_state: str | None = None,
	planning_filters: dict | str | None = None,
	additional_filters: list | str | None = None,
	required_vehicle_category: str | None = None,
	selected_delivery_order_names: list | str | None = None,
	start: int = 0,
	page_length: int = 50,
	required_delivery_date: str | None = None,
):
	"""Return a paginated demand list and bounded warehouse summaries."""
	require_doctype_permission(source.doctype(), "read")
	require_company_access(company)
	if required_vehicle_category:
		require_doctype_permission("Vehicle Category", "read")
	configured_filters = validate_configured_filters(company, planning_filters)
	additional_filter_conditions = validate_additional_filters(additional_filters)
	territory = territory or configured_filters.get("territory")
	county = county or configured_filters.get("county")
	town = town or configured_filters.get("town")
	constituency = constituency or configured_filters.get("constituency")
	delivery_address = delivery_address or configured_filters.get("delivery_address")
	selected_delivery_order_names = (
		frappe.parse_json(selected_delivery_order_names)
		if isinstance(selected_delivery_order_names, str)
		else selected_delivery_order_names
	)
	if len(selected_delivery_order_names or []) > MAX_SELECTED_ORDERS:
		frappe.throw(_("Select no more than {0} transport demands at a time.").format(MAX_SELECTED_ORDERS))
	start = max(cint(start), 0)
	page_length = max(1, min(cint(page_length or 50), MAX_PAGE_LENGTH))
	filters = _planning_filters(
		company,
		required_delivery_date_from or required_delivery_date,
		required_delivery_date_to or required_delivery_date,
		source_warehouse,
		transport_zone,
		delivery_method,
		priority,
		customer,
		delivery_address,
		territory,
		county,
		town,
		constituency,
	)
	for fieldname in (
		"delivery_group",
		"transport_compatibility_group",
		"preferred_route",
		"maximum_vehicle_category",
		"transport_handling_profile",
	):
		if configured_filters.get(fieldname):
			filters[fieldname] = configured_filters[fieldname]
	warehouse_options = {}
	if source.doctype() == "Transport Demand" and source_warehouse:
		filters.pop("source_warehouse", None)
		warehouse_options["or_filters"] = [
			["source_warehouse", "=", source_warehouse],
			["source_warehouse", "is", "not set"],
		]
	orders = frappe.get_list(
		source.doctype(),
		filters=as_filter_conditions(filters, additional_filter_conditions),
		fields=source.fields(_delivery_order_fields()),
		order_by=f"{source.field('required_delivery_date')} asc, priority desc, creation asc",
		limit_page_length=MAX_SUMMARY_ORDERS + 1,
		**warehouse_options,
	)
	pool_truncated = len(orders) > MAX_SUMMARY_ORDERS
	orders = orders[:MAX_SUMMARY_ORDERS]
	metrics = _get_order_metrics([row.name for row in orders], source_warehouse=source_warehouse)
	all_rows = [_format_demand_row(order, metrics.get(order.name)) for order in orders]
	all_rows = _filter_allocation_state(all_rows, allocation_state)
	filtered_rows = _filter_vehicle_category(all_rows, required_vehicle_category)
	all_page_rows = _keep_selected_orders_visible(all_rows, filtered_rows, selected_delivery_order_names)
	has_more = pool_truncated or len(all_page_rows) > start + page_length
	rows = all_page_rows[start : start + page_length]
	_add_delivery_locations(rows)
	summary_rows = all_rows
	summary_rows = _filter_allocation_state(summary_rows, allocation_state)
	summary_rows = _keep_selected_orders_visible(
		all_rows,
		_filter_vehicle_category(summary_rows, required_vehicle_category),
		selected_delivery_order_names,
	)
	return {
		"orders": rows,
		"warehouse_summaries": _warehouse_summaries(summary_rows),
		"start": start,
		"page_length": page_length,
		"has_more": has_more,
		"summary_truncated": pool_truncated,
		"summary_limit": MAX_SUMMARY_ORDERS,
	}


@frappe.whitelist()
def get_dispatch_planning_filter_definitions(company: str | None = None):
	"""Return administrator-configured additional filters for the planner."""
	require_doctype_permission(source.doctype(), "read")
	if company:
		require_company_access(company)
	return get_filter_definitions()


@frappe.whitelist()
def get_delivery_order_details(delivery_order_names: list | str, source_warehouse: str | None = None):
	"""Return item-level balances for a bounded set of readable Delivery Orders."""
	require_doctype_permission(source.doctype(), "read")
	names = _parse_names(delivery_order_names, MAX_DETAIL_ORDERS)
	orders = frappe.get_list(
		source.doctype(),
		filters=source.filters({"name": ["in", names], "docstatus": 1}),
		fields=source.fields(_delivery_order_fields()),
		limit_page_length=MAX_DETAIL_ORDERS,
	)
	if len(orders) != len(names):
		frappe.throw(
			_("One or more Delivery Orders are unavailable or not permitted."),
			frappe.PermissionError,
		)
	for company in {order.company for order in orders}:
		require_company_access(company)
	metrics = _get_order_metrics(names, include_items=True, source_warehouse=source_warehouse)
	return [
		{
			**_format_demand_row(order, metrics.get(order.name)),
			"items": metrics.get(order.name, {}).get("items", []),
		}
		for order in orders
	]


@frappe.whitelist()
def evaluate_manual_load_plan(
	allocations: list | str, vehicle_category: str | None = None, source_warehouse: str | None = None
):
	"""Recalculate a proposed manual load without reserving or saving anything."""
	require_doctype_permission("Transport Load", "create")
	load = build_delivery_load_from_allocations(
		allocations, vehicle_category, source_warehouse=source_warehouse
	)
	validate_load_allocations(load, lock=False)
	calculate_load_totals(load)
	compatible_names = get_compatible_vehicle_category_names(load)
	recommended = recommend_vehicle_category(load) if compatible_names else None
	categories = get_vehicle_categories(
		{"weight_uom": load.weight_uom, "volume_uom": load.volume_uom},
		get_capacity_buffer_percentage(),
	)
	category_rows = []
	for category in categories:
		if category.name not in compatible_names and category.name != vehicle_category:
			continue
		result = evaluate_vehicle_category(_load_totals(load), category, _load_requirements(load))
		category_rows.append(
			{
				"name": category.name,
				"label": category.vehicle_category_name or category.name,
				"recommended": category.name == recommended,
				"utilization": result["utilization"],
				"compatible": result["compatible"],
				"capacity": get_capacity_breakdown(_load_totals(load), category),
			}
		)
	return {
		"company": load.company,
		"source_warehouse": load.source_warehouse,
		"dispatch_date": load.dispatch_date,
		"delivery_method": load.delivery_method,
		"transport_zone": load.transport_zone,
		"totals": _load_totals(load),
		"total_stops": load.total_stops,
		"weight_uom": load.weight_uom,
		"volume_uom": load.volume_uom,
		"requirements": _load_requirements(load),
		"compatible_vehicle_categories": category_rows,
		"selected_vehicle_category_capacity": next(
			(row["capacity"] for row in category_rows if row["name"] == vehicle_category),
			None,
		),
		"recommended_vehicle_category": recommended,
		"selected_vehicle_category_compatible": (
			vehicle_category in compatible_names if vehicle_category else None
		),
		"allocations": [
			{
				source.allocation_fields()[0]: row.get(source.allocation_fields()[0]),
				source.allocation_fields()[1]: row.get(source.allocation_fields()[1]),
				"allocated_quantity": row.allocated_quantity,
				"available_quantity": row.available_quantity,
				"weight": row.weight,
				"volume": row.volume,
				"pallets": row.pallets,
				"cases": row.cases,
				"crates": row.crates,
			}
			for row in load.delivery_orders
		],
	}


@frappe.whitelist()
def create_delivery_load_from_plan(
	allocations: list | str, vehicle_category: str | None = None, source_warehouse: str | None = None
):
	return _create_delivery_load_from_plan(allocations, vehicle_category, source_warehouse=source_warehouse)


@frappe.whitelist()
def get_fleet_availability_summary(company: str, from_time: str, to_time: str):
	require_doctype_permission("Vehicle", "read")
	require_company_access(company)
	results = _permitted_vehicle_availability(company, from_time, to_time)
	return {
		"categories": _summarize_vehicle_categories(results.values()),
		"total": len(results),
		"from_time": from_time,
		"to_time": to_time,
	}


@frappe.whitelist()
def get_vehicle_availability_details(company: str, vehicle_category: str, from_time: str, to_time: str):
	require_doctype_permission("Vehicle", "read")
	require_company_access(company)
	results = _permitted_vehicle_availability(company, from_time, to_time, vehicle_category)
	return list(results.values())


@frappe.whitelist()
def get_costed_vehicle_candidates(
	allocations: list | str, vehicle_category: str | None = None, source_warehouse: str | None = None
):
	"""Return eligible physical vehicles ranked by estimated marginal cost."""
	require_doctype_permission("Transport Load", "read")
	require_doctype_permission("Vehicle", "read")
	load = build_delivery_load_from_allocations(
		allocations, vehicle_category, source_warehouse=source_warehouse
	)
	validate_load_allocations(load, lock=False)
	calculate_load_totals(load)
	category = vehicle_category or recommend_vehicle_category(load)
	if not category:
		return []
	from_time, to_time = get_load_resource_window(load)
	results = get_vehicle_availability_map(
		company=load.company,
		vehicle_category=category,
		from_time=from_time,
		to_time=to_time,
		required_vehicle_category=category,
		delivery_load=load,
		expected_company=load.company,
	)
	vehicles = {
		row.name: frappe.get_cached_doc("Vehicle", row.name)
		for row in frappe.get_all(
			"Vehicle",
			filters={"name": ["in", list(results)]},
			fields=["name"],
			limit_page_length=100,
		)
	}
	candidates = []
	for name, availability in results.items():
		if not availability.dispatch_eligible or name not in vehicles:
			continue
		estimate = estimate_transport_cost(load, vehicles[name])
		candidates.append(
			{
				"vehicle": name,
				"license_plate": vehicles[name].license_plate or name,
				"vehicle_category": availability.vehicle_category,
				"operational_state": availability.operational_state,
				"dispatch_eligible": availability.dispatch_eligible,
				"cost_structure": estimate.get("cost_structure"),
				"cost_structure_assignment": estimate.get("cost_structure_assignment"),
				"currency": estimate.get("currency"),
				"estimated_distance": estimate.get("distance"),
				"distance_uom": estimate.get("distance_uom"),
				"marginal_cost": estimate.get("marginal_cost"),
				"estimated_full_cost": estimate.get("estimated_full_cost"),
				"cost_breakdown": estimate.get("components", []),
				"warnings": estimate.get("warnings", []),
			}
		)
	return _rank_costed_vehicles(candidates)


def _rank_costed_vehicles(candidates):
	currencies = {row.get("currency") for row in candidates if row.get("currency")}
	if len(currencies) > 1:
		for row in candidates:
			row.setdefault("warnings", []).append(
				_("Vehicle estimates use different currencies. Compare costs within the same currency only.")
			)
	return sorted(
		candidates,
		key=lambda row: (
			not bool(row.get("cost_structure_assignment")),
			row.get("currency") or "",
			row["marginal_cost"] is None,
			row["marginal_cost"],
			row["vehicle"],
		),
	)


@frappe.whitelist()
def get_cost_structure_estimates(
	allocations: list | str, vehicle_category: str | None = None, source_warehouse: str | None = None
):
	"""Compare applicable Cost Structures before a physical Vehicle is selected."""
	require_doctype_permission("Transport Load", "read")
	require_doctype_permission("Transport Cost Structure", "read")
	load = build_delivery_load_from_allocations(
		allocations, vehicle_category, source_warehouse=source_warehouse
	)
	validate_load_allocations(load, lock=False)
	calculate_load_totals(load)
	return _cost_structure_estimates_for_load(load, vehicle_category)


@frappe.whitelist()
def get_transport_load_cost_estimates(transport_load: str) -> dict:
	"""Preview any saved movement type without reserving cargo or changing the load."""
	require_doctype_permission("Transport Cost Structure", "read")
	load = frappe.get_doc("Transport Load", transport_load)
	require_document_permission(load, "read")
	require_company_access(load.company)
	if not frappe.db.get_single_value("Transport Settings", "enable_transport_costing"):
		frappe.throw(_("Enable Transport Costing in Transport Settings before estimating costs."))
	return _cost_structure_estimates_for_load(load, load.required_vehicle_category)


def _cost_structure_estimates_for_load(load, vehicle_category=None):
	"""Share rate selection and explanations across planning and saved-load previews."""
	structures = frappe.get_all(
		"Transport Cost Structure",
		filters={"disabled": 0, "docstatus": 1},
		or_filters=[{"company": load.company}, {"company": ["is", "not set"]}],
		fields=["name", "cost_structure_name", "vehicle_owner"],
		order_by="cost_structure_name asc, name asc",
		limit_page_length=100,
	)
	results = []
	for structure in structures:
		estimate = estimate_transport_cost_for_cost_structure(
			load,
			structure.name,
			vehicle_category=vehicle_category,
		)
		results.append(
			{
				"cost_structure": structure.name,
				"cost_structure_label": structure.cost_structure_name or structure.name,
				"vehicle_owner": structure.vehicle_owner,
				"cost_structure_assignment": estimate.get("cost_structure_assignment"),
				"currency": estimate.get("currency"),
				"estimated_distance": estimate.get("distance"),
				"distance_uom": estimate.get("distance_uom"),
				"distance_source": estimate.get("distance_source"),
				"marginal_cost": estimate.get("marginal_cost"),
				"estimated_full_cost": estimate.get("estimated_full_cost"),
				"cost_breakdown": estimate.get("components", []),
				"warnings": estimate.get("warnings", []),
			}
		)
	available = [row for row in results if row["cost_structure_assignment"] and not row["warnings"]]
	if not available:
		available = [row for row in results if row["cost_structure_assignment"]]
	currencies = {row["currency"] for row in results if row["currency"]}
	if available and len(currencies) == 1:
		recommended = min(available, key=lambda row: (row["estimated_full_cost"], row["cost_structure"]))
		for row in results:
			row["recommended"] = row["cost_structure"] == recommended["cost_structure"]
	return {
		"distance": getattr(load, "estimated_distance", None),
		"distance_uom": results[0].get("distance_uom") if results else None,
		"warnings": [_("Estimates use different currencies. Compare amounts within the same currency only.")]
		if len(currencies) > 1
		else [],
		"cost_structures": sorted(
			results,
			key=lambda row: (
				not bool(row["cost_structure_assignment"]),
				row["currency"] or "",
				row["estimated_full_cost"],
				row["cost_structure"],
			),
		),
	}


@frappe.whitelist()
def get_driver_availability_summary(company: str, from_time: str, to_time: str):
	require_doctype_permission("Driver", "read")
	require_company_access(company)
	results = _permitted_driver_availability(from_time, to_time, company)
	states = defaultdict(int)
	for result in results.values():
		states[result.operational_state] += 1
	return {
		"total": len(results),
		"active": sum(1 for result in results.values() if result.driver_status == "Active"),
		"available": states["Available"],
		"allocated": states["Allocated"],
		"in_trip": states["In Trip"],
		"unavailable": states["Unavailable"],
	}


@frappe.whitelist()
def get_driver_availability_details(company: str, from_time: str, to_time: str):
	require_doctype_permission("Driver", "read")
	require_company_access(company)
	return list(_permitted_driver_availability(from_time, to_time, company).values())


def _planning_filters(
	company,
	required_delivery_date_from=None,
	required_delivery_date_to=None,
	source_warehouse=None,
	transport_zone=None,
	delivery_method=None,
	priority=None,
	customer=None,
	delivery_address=None,
	territory=None,
	county=None,
	town=None,
	constituency=None,
):
	filters = {
		"company": company,
		"docstatus": 1,
		"status": ["in", PLANNABLE_STATUSES],
	}
	from vuna_transport.services.demand.delivery_order_service import get_open_sales_order_names

	filters["sales_order"] = ["in", get_open_sales_order_names(company)]
	date_filter = _delivery_date_filter(required_delivery_date_from, required_delivery_date_to)
	if date_filter:
		filters["required_delivery_date"] = date_filter
	for fieldname, value in {
		"source_warehouse": source_warehouse,
		"transport_zone": transport_zone,
		"delivery_method": delivery_method,
		"priority": priority,
		"customer": customer,
		"delivery_address": delivery_address,
		"territory": territory,
		"county": county,
		"town": town,
		"constituency": constituency,
	}.items():
		if value:
			filters[fieldname] = value
	return filters


def _get_address_names(delivery_address=None, territory=None, county=None, constituency=None, town=None):
	"""Resolve planner geography filters to permitted Address names."""
	if not any((delivery_address, territory, county, constituency, town)):
		return None
	filters = {}
	if delivery_address:
		filters["name"] = delivery_address
	if territory:
		filters["vuna_territory"] = territory
	if county:
		filters["vuna_county"] = county
	if constituency:
		filters["vuna_constituency"] = constituency
	if town:
		filters["vuna_town"] = town
	return frappe.get_list("Address", filters=filters, pluck="name", limit_page_length=5000)


def _delivery_date_filter(date_from=None, date_to=None):
	if date_from and date_to and getdate(date_from) > getdate(date_to):
		frappe.throw(_("Delivery Date From cannot be after Delivery Date To."))
	if date_from and date_to:
		return ["between", [getdate(date_from), getdate(date_to)]]
	if date_from:
		return [">=", getdate(date_from)]
	if date_to:
		return ["<=", getdate(date_to)]
	return None


def _delivery_order_fields():
	return [
		"name",
		"company",
		"customer",
		"customer_name",
		"source_warehouse",
		"required_delivery_date",
		"transport_zone",
		"preferred_route",
		"delivery_method",
		"delivery_group",
		"transport_compatibility_group",
		"priority",
		"delivery_address",
		"territory",
		"county",
		"constituency",
		"town",
		"status",
		"transport_handling_profile",
		"refrigeration_required",
		"tail_lift_required",
		"required_body_type",
		"special_equipment_required",
		"maximum_vehicle_category",
		"restricted_access",
		"access_notes",
		"weight_uom",
		"volume_uom",
	]


def _get_order_metrics(order_names, include_items=False, source_warehouse=None):
	order_names = list(dict.fromkeys(order_names or []))
	if not order_names:
		return {}
	if source.doctype() == "Transport Demand":
		return source.metrics(order_names, include_items=include_items, source_warehouse=source_warehouse)
	items = frappe.get_all(
		"Delivery Order Item",
		filters={"parent": ["in", order_names]},
		fields=[
			"name",
			"parent",
			"idx",
			"item_code",
			"item_name",
			"transport_qty",
			"uom",
			"conversion_factor",
			"stock_qty",
			"stock_uom",
			"weight",
			"weight_uom",
			"volume",
			"volume_uom",
			"pallets",
			"cases",
			"crates",
			"transport_handling_profile",
		],
		order_by="parent asc, idx asc",
	)
	warehouses_by_order = {
		row.name: row.source_warehouse
		for row in frappe.get_all(
			"Delivery Order", filters={"name": ["in", order_names]}, fields=["name", "source_warehouse"]
		)
	}
	bins = (
		{
			(row.item_code, row.warehouse): flt(row.actual_qty)
			for row in frappe.get_all(
				"Bin",
				filters={
					"item_code": ["in", sorted({item.item_code for item in items if item.item_code})],
					"warehouse": ["in", sorted(set(warehouses_by_order.values()) - {None, ""})],
				},
				fields=["item_code", "warehouse", "actual_qty"],
			)
		}
		if items and any(warehouses_by_order.values())
		else {}
	)
	allocation_rows = frappe.db.sql(
		"""
			select
				dlo.delivery_order_item,
				coalesce(sum(case when dl.docstatus = 0 then dlo.allocated_quantity else 0 end), 0) as reserved_quantity,
				coalesce(sum(case when dl.docstatus = 1 then dlo.allocated_quantity else 0 end), 0) as allocated_quantity
			from `tabTransport Load Item` dlo
			join `tabTransport Load` dl on dl.name = dlo.parent
			where dlo.delivery_order in %(order_names)s
			and dl.docstatus in (0, 1)
			group by dlo.delivery_order_item
		""",
		{"order_names": tuple(order_names)},
		as_dict=True,
	)
	allocations = {row.delivery_order_item: row for row in allocation_rows}
	result = {
		name: {
			"requested_quantity": 0.0,
			"reserved_quantity": 0.0,
			"allocated_quantity": 0.0,
			"unallocated_quantity": 0.0,
			**{dimension: 0.0 for dimension in CAPACITY_DIMENSIONS},
			"items": [],
		}
		for name in order_names
	}
	for item in items:
		allocation = allocations.get(item.name) or frappe._dict()
		requested = flt(item.transport_qty)
		reserved = flt(allocation.reserved_quantity)
		allocated = flt(allocation.allocated_quantity)
		available = max(requested - reserved - allocated, 0)
		ratio = available / requested if requested else 0
		metrics = {
			"weight": flt(item.weight) * ratio,
			"volume": flt(item.volume) * ratio,
			"pallets": flt(item.pallets) * ratio,
			"cases": flt(item.cases) * ratio,
			"crates": flt(item.crates) * ratio,
		}
		order = result[item.parent]
		order["requested_quantity"] += requested
		order["reserved_quantity"] += reserved
		order["allocated_quantity"] += allocated
		order["unallocated_quantity"] += available
		for dimension in CAPACITY_DIMENSIONS:
			order[dimension] += metrics[dimension]
		if include_items:
			stock_required_quantity = available * flt(item.conversion_factor or 1)
			stock_available_quantity = bins.get((item.item_code, warehouses_by_order.get(item.parent)), 0.0)
			order["items"].append(
				{
					"name": item.name,
					"item_code": item.item_code,
					"item_name": item.item_name,
					"transport_quantity": requested,
					"uom": item.uom,
					"conversion_factor": item.conversion_factor,
					"stock_quantity": flt(item.stock_qty) * ratio,
					"stock_uom": item.stock_uom,
					"reserved_quantity": reserved,
					"allocated_quantity": allocated,
					"available_quantity": available,
					"stock_available_quantity": stock_available_quantity,
					"stock_warehouse": warehouses_by_order.get(item.parent),
					"stock_required_quantity": stock_required_quantity,
					"stock_availability_status": (
						"Available" if stock_available_quantity >= stock_required_quantity else "Insufficient"
					),
					"weight": metrics["weight"],
					"weight_uom": item.weight_uom,
					"volume": metrics["volume"],
					"volume_uom": item.volume_uom,
					"pallets": metrics["pallets"],
					"cases": metrics["cases"],
					"crates": metrics["crates"],
					"handling_requirements": item.transport_handling_profile,
				}
			)
	return result


def _add_delivery_locations(rows):
	"""Resolve only the visible page's linked destinations, respecting Address permissions."""
	for row in rows:
		row.update(
			delivery_address_summary="",
			delivery_city="",
			delivery_address_type="",
			delivery_address_status="Unavailable" if row.get("delivery_address") else "Missing",
		)
	names = sorted({row["delivery_address"] for row in rows if row.get("delivery_address")})
	if not names or not frappe.has_permission("Address", "read"):
		return
	addresses = {
		address.name: address
		for address in frappe.get_list(
			"Address",
			filters={"name": ["in", names]},
			fields=["name", "address_title", "address_line1", "address_line2", "city", "address_type"],
			limit_page_length=len(names),
		)
	}
	for row in rows:
		address = addresses.get(row.get("delivery_address"))
		if not address:
			continue
		parts = []
		seen = set()
		for field in ("address_title", "address_line1", "address_line2", "city"):
			value = (address.get(field) or "").strip()
			if value and value.casefold() not in seen:
				parts.append(value)
				seen.add(value.casefold())
		row.update(
			delivery_address_summary=", ".join(parts),
			delivery_city=address.city,
			delivery_address_type=address.address_type,
			delivery_address_status="Available" if parts else "Incomplete",
		)


def _format_demand_row(order, metrics):
	metrics = metrics or {}
	return {
		"name": order.name,
		"customer": order.customer,
		"customer_name": order.customer_name,
		"source_warehouse": order.source_warehouse,
		"required_delivery_date": order.required_delivery_date,
		"transport_zone": order.transport_zone,
		"preferred_route": order.preferred_route,
		"delivery_method": order.delivery_method,
		"delivery_group": getattr(order, "delivery_group", None),
		"transport_compatibility_group": getattr(order, "transport_compatibility_group", None),
		"priority": order.priority,
		"delivery_address": order.delivery_address,
		"territory": order.territory,
		"county": order.county,
		"constituency": order.constituency,
		"town": order.town,
		"status": order.status,
		"requested_quantity": flt(metrics.get("requested_quantity")),
		"reserved_quantity": flt(metrics.get("reserved_quantity")),
		"allocated_quantity": flt(metrics.get("allocated_quantity")),
		"unallocated_quantity": flt(metrics.get("unallocated_quantity")),
		"weight": flt(metrics.get("weight")),
		"weight_uom": order.weight_uom,
		"volume": flt(metrics.get("volume")),
		"volume_uom": order.volume_uom,
		"pallets": flt(metrics.get("pallets")),
		"cases": flt(metrics.get("cases")),
		"crates": flt(metrics.get("crates")),
		"transport_handling_profile": order.transport_handling_profile,
		"refrigeration_required": bool(order.refrigeration_required),
		"tail_lift_required": bool(order.tail_lift_required),
		"required_body_type": order.required_body_type,
		"special_equipment_required": order.special_equipment_required,
		"maximum_vehicle_category": order.maximum_vehicle_category,
		"restricted_access": bool(order.restricted_access),
		"access_notes": order.access_notes,
	}


def _filter_allocation_state(rows, allocation_state):
	if not allocation_state:
		return [row for row in rows if row["unallocated_quantity"] > 0]
	if allocation_state == "Unallocated":
		return [
			row
			for row in rows
			if row["unallocated_quantity"] > 0
			and not row["allocated_quantity"]
			and not row["reserved_quantity"]
		]
	if allocation_state == "Partially Allocated":
		return [
			row
			for row in rows
			if row["unallocated_quantity"] > 0 and (row["allocated_quantity"] or row["reserved_quantity"])
		]
	return [row for row in rows if row["unallocated_quantity"] > 0]


def _filter_vehicle_category(rows, vehicle_category):
	if not vehicle_category:
		return rows
	category = frappe._dict(frappe.get_cached_doc("Vehicle Category", vehicle_category).as_dict())
	if category.disabled:
		return []
	filtered = []
	for row in rows:
		capacities = get_effective_capacities(
			category,
			{"weight_uom": row.get("weight_uom"), "volume_uom": row.get("volume_uom")},
			0,
		)
		category.capacities = capacities
		result = evaluate_vehicle_category(
			{dimension: row.get(dimension) for dimension in CAPACITY_DIMENSIONS},
			category,
			{
				"refrigeration_required": row.get("refrigeration_required"),
				"tail_lift_required": row.get("tail_lift_required"),
				"required_body_type": row.get("required_body_type"),
				"special_equipment_required": row.get("special_equipment_required"),
			},
		)
		if (
			result["compatible"]
			and validate_vehicle_access_limit(category, row.get("maximum_vehicle_category"))["compatible"]
		):
			filtered.append(row)
	return filtered


def _keep_selected_orders_visible(all_rows, filtered_rows, selected_order_names):
	"""Keep selected rows visible when a category filter changes their eligibility."""
	if not selected_order_names:
		return filtered_rows
	selected_order_names = (
		frappe.parse_json(selected_order_names)
		if isinstance(selected_order_names, str)
		else selected_order_names
	)
	selected = set(selected_order_names or [])
	visible_names = {row["name"] for row in filtered_rows}
	selected_rows = []
	for row in all_rows:
		if row["name"] in selected and row["name"] not in visible_names:
			selected_row = dict(row)
			selected_row["vehicle_category_incompatible"] = True
			selected_rows.append(selected_row)
	return filtered_rows + selected_rows


def _warehouse_summaries(rows):
	summaries = defaultdict(
		lambda: {
			"source_warehouse": None,
			"delivery_order_count": 0,
			"unallocated_quantity": 0.0,
			**{dimension: 0.0 for dimension in CAPACITY_DIMENSIONS},
		}
	)
	for row in rows:
		summary = summaries[row["source_warehouse"]]
		summary["source_warehouse"] = row["source_warehouse"]
		summary["delivery_order_count"] += 1
		summary["unallocated_quantity"] += row["unallocated_quantity"]
		for dimension in CAPACITY_DIMENSIONS:
			summary[dimension] += flt(row.get(dimension))
	return list(summaries.values())


def _permitted_vehicle_availability(company, from_time, to_time, vehicle_category=None):
	filters = {"company": ["in", [company, ""]]}
	if vehicle_category:
		filters["vuna_vehicle_category"] = vehicle_category
	vehicle_names = frappe.get_list(
		"Vehicle",
		filters=filters,
		pluck="name",
		limit_page_length=1000,
	)
	return get_vehicle_availability_map(
		vehicle_names=vehicle_names,
		from_time=from_time,
		to_time=to_time,
		expected_company=company,
	)


def _permitted_driver_availability(from_time, to_time, company=None):
	driver_names = frappe.get_list("Driver", pluck="name", limit_page_length=1000)
	return get_driver_availability_map(
		driver_names=driver_names,
		from_time=from_time,
		to_time=to_time,
		company=company,
	)


def _summarize_vehicle_categories(results):
	summaries = defaultdict(
		lambda: {
			"vehicle_category": None,
			"vehicle_category_label": None,
			"total": 0,
			"available": 0,
			"allocated": 0,
			"loading": 0,
			"ready_for_dispatch": 0,
			"in_trip": 0,
			"under_maintenance": 0,
			"compliance_blocked": 0,
			"unavailable": 0,
		}
	)
	for result in results:
		if not result.vehicle_category:
			continue
		summary = summaries[result.vehicle_category]
		summary["vehicle_category"] = result.vehicle_category
		summary["vehicle_category_label"] = (
			getattr(result, "vehicle_category_label", None) or result.vehicle_category
		)
		summary["total"] += 1
		summary[result.operational_state.lower().replace(" ", "_")] += 1
		if getattr(result, "compliance_status", "Compliant") == "Blocked":
			summary["compliance_blocked"] += 1
	return list(summaries.values())


def _load_totals(load):
	return {
		"weight": flt(load.total_weight),
		"volume": flt(load.total_volume),
		"pallets": flt(load.total_pallets),
		"cases": flt(load.total_cases),
		"crates": flt(load.total_crates),
	}


def _load_requirements(load):
	return {
		"refrigeration_required": bool(load.refrigeration_required),
		"tail_lift_required": bool(load.tail_lift_required),
		"required_body_type": load.required_body_type,
		"special_equipment_required": load.special_equipment_required,
	}


def _parse_names(names, maximum):
	names = frappe.parse_json(names) if isinstance(names, str) else names
	if not isinstance(names, list) or not names:
		frappe.throw(_("Select at least one transport demand."))
	names = list(dict.fromkeys(names))
	if len(names) > maximum:
		frappe.throw(_("Select no more than {0} transport demands at once.").format(maximum))
	return names
