"""Bounded, permission-aware APIs for the Dispatch Planning Desk page."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt

from vuna_transport.services.delivery_load_service import (
	build_delivery_load_from_allocations,
	calculate_load_totals,
	create_delivery_load_from_plan as _create_delivery_load_from_plan,
	get_capacity_buffer_percentage,
	get_compatible_vehicle_category_names,
	recommend_vehicle_category,
)
from vuna_transport.services.driver_availability_service import (
	get_driver_availability_map,
)
from vuna_transport.services.load_validation_service import validate_load_allocations
from vuna_transport.services.security_service import (
	require_company_access,
	require_doctype_permission,
	require_document_permission,
)
from vuna_transport.services.vehicle_availability_service import (
	get_vehicle_availability_map,
)
from vuna_transport.services.vehicle_category_service import (
	evaluate_vehicle_category,
	get_vehicle_categories,
)


MAX_PAGE_LENGTH = 100
MAX_SUMMARY_ORDERS = 500
MAX_DETAIL_ORDERS = 50
PLANNABLE_STATUSES = ("Open", "Partially Allocated")
CAPACITY_DIMENSIONS = ("weight", "volume", "pallets", "cases", "crates")


@frappe.whitelist()
def get_planning_demand(
	company,
	required_delivery_date=None,
	source_warehouse=None,
	transport_zone=None,
	delivery_method=None,
	priority=None,
	customer=None,
	allocation_state=None,
	start=0,
	page_length=50,
):
	"""Return a paginated demand list and bounded warehouse summaries."""
	require_doctype_permission("Delivery Order", "read")
	require_company_access(company)
	start = max(cint(start), 0)
	page_length = max(1, min(cint(page_length or 50), MAX_PAGE_LENGTH))
	filters = _planning_filters(
		company,
		required_delivery_date,
		source_warehouse,
		transport_zone,
		delivery_method,
		priority,
		customer,
	)
	page_orders = frappe.get_list(
		"Delivery Order",
		filters=filters,
		fields=_delivery_order_fields(),
		order_by="required_delivery_date asc, priority desc, creation asc",
		limit_start=start,
		limit_page_length=page_length + 1,
	)
	has_more = len(page_orders) > page_length
	page_orders = page_orders[:page_length]
	page_metrics = _get_order_metrics([row.name for row in page_orders])
	rows = [
		_format_demand_row(order, page_metrics.get(order.name))
		for order in page_orders
	]
	rows = _filter_allocation_state(rows, allocation_state)

	summary_orders = frappe.get_list(
		"Delivery Order",
		filters=filters,
		fields=_delivery_order_fields(),
		order_by="source_warehouse asc, required_delivery_date asc",
		limit_page_length=MAX_SUMMARY_ORDERS + 1,
	)
	summary_truncated = len(summary_orders) > MAX_SUMMARY_ORDERS
	summary_orders = summary_orders[:MAX_SUMMARY_ORDERS]
	summary_metrics = _get_order_metrics([row.name for row in summary_orders])
	summary_rows = [
		_format_demand_row(order, summary_metrics.get(order.name))
		for order in summary_orders
	]
	summary_rows = _filter_allocation_state(summary_rows, allocation_state)
	return {
		"orders": rows,
		"warehouse_summaries": _warehouse_summaries(summary_rows),
		"start": start,
		"page_length": page_length,
		"has_more": has_more,
		"summary_truncated": summary_truncated,
		"summary_limit": MAX_SUMMARY_ORDERS,
	}


@frappe.whitelist()
def get_delivery_order_details(delivery_order_names):
	"""Return item-level balances for a bounded set of readable Delivery Orders."""
	require_doctype_permission("Delivery Order", "read")
	names = _parse_names(delivery_order_names, MAX_DETAIL_ORDERS)
	orders = frappe.get_list(
		"Delivery Order",
		filters={"name": ["in", names]},
		fields=_delivery_order_fields(),
		limit_page_length=MAX_DETAIL_ORDERS,
	)
	if len(orders) != len(names):
		frappe.throw(
			_("One or more Delivery Orders are unavailable or not permitted."),
			frappe.PermissionError,
		)
	for company in {order.company for order in orders}:
		require_company_access(company)
	metrics = _get_order_metrics(names, include_items=True)
	return [
		{
			**_format_demand_row(order, metrics.get(order.name)),
			"items": metrics.get(order.name, {}).get("items", []),
		}
		for order in orders
	]


@frappe.whitelist()
def evaluate_manual_load_plan(allocations, vehicle_category=None):
	"""Recalculate a proposed manual load without reserving or saving anything."""
	require_doctype_permission("Delivery Load", "create")
	load = build_delivery_load_from_allocations(allocations, vehicle_category)
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
		if category.name not in compatible_names:
			continue
		result = evaluate_vehicle_category(
			_load_totals(load), category, _load_requirements(load)
		)
		category_rows.append(
			{
				"name": category.name,
				"label": category.vehicle_category_name or category.name,
				"recommended": category.name == recommended,
				"utilization": result["utilization"],
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
		"recommended_vehicle_category": recommended,
		"selected_vehicle_category_compatible": (
			vehicle_category in compatible_names if vehicle_category else None
		),
		"allocations": [
			{
				"delivery_order": row.delivery_order,
				"delivery_order_item": row.delivery_order_item,
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
def create_delivery_load_from_plan(allocations, vehicle_category=None):
	return _create_delivery_load_from_plan(allocations, vehicle_category)


@frappe.whitelist()
def get_fleet_availability_summary(company, from_time, to_time):
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
def get_vehicle_availability_details(
	company, vehicle_category, from_time, to_time
):
	require_doctype_permission("Vehicle", "read")
	require_company_access(company)
	results = _permitted_vehicle_availability(
		company, from_time, to_time, vehicle_category
	)
	return list(results.values())


@frappe.whitelist()
def get_driver_availability_summary(company, from_time, to_time):
	require_doctype_permission("Driver", "read")
	require_company_access(company)
	results = _permitted_driver_availability(from_time, to_time, company)
	states = defaultdict(int)
	for result in results.values():
		states[result.operational_state] += 1
	return {
		"total": len(results),
		"active": sum(
			1 for result in results.values() if result.driver_status == "Active"
		),
		"available": states["Available"],
		"allocated": states["Allocated"],
		"in_trip": states["In Trip"],
		"unavailable": states["Unavailable"],
	}


@frappe.whitelist()
def get_driver_availability_details(company, from_time, to_time):
	require_doctype_permission("Driver", "read")
	require_company_access(company)
	return list(_permitted_driver_availability(from_time, to_time, company).values())


def _planning_filters(
	company,
	required_delivery_date,
	source_warehouse,
	transport_zone,
	delivery_method,
	priority,
	customer,
):
	filters = {
		"company": company,
		"docstatus": 1,
		"status": ["in", PLANNABLE_STATUSES],
	}
	for fieldname, value in {
		"required_delivery_date": required_delivery_date,
		"source_warehouse": source_warehouse,
		"transport_zone": transport_zone,
		"delivery_method": delivery_method,
		"priority": priority,
		"customer": customer,
	}.items():
		if value:
			filters[fieldname] = value
	return filters


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
		"priority",
		"delivery_address",
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


def _get_order_metrics(order_names, include_items=False):
	order_names = list(dict.fromkeys(order_names or []))
	if not order_names:
		return {}
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
	allocation_rows = frappe.db.sql(
		"""
			select
				dlo.delivery_order_item,
				coalesce(sum(case when dl.docstatus = 0 then dlo.allocated_quantity else 0 end), 0) as reserved_quantity,
				coalesce(sum(case when dl.docstatus = 1 then dlo.allocated_quantity else 0 end), 0) as allocated_quantity
			from `tabDelivery Load Order` dlo
			join `tabDelivery Load` dl on dl.name = dlo.parent
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
			order["items"].append(
				{
					"name": item.name,
					"item_code": item.item_code,
					"item_name": item.item_name,
					"transport_quantity": requested,
					"stock_uom": item.stock_uom,
					"reserved_quantity": reserved,
					"allocated_quantity": allocated,
					"available_quantity": available,
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
		"priority": order.priority,
		"delivery_address": order.delivery_address,
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
			if row["unallocated_quantity"] > 0
			and (row["allocated_quantity"] or row["reserved_quantity"])
		]
	return [row for row in rows if row["unallocated_quantity"] > 0]


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


def _permitted_vehicle_availability(
	company, from_time, to_time, vehicle_category=None
):
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
	driver_names = frappe.get_list(
		"Driver", pluck="name", limit_page_length=1000
	)
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
		frappe.throw(_("Select at least one Delivery Order."))
	names = list(dict.fromkeys(names))
	if len(names) > maximum:
		frappe.throw(_("Select no more than {0} Delivery Orders at once.").format(maximum))
	return names
