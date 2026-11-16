"""Read-only, deterministic Transport Load suggestions and secure acceptance."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate

from vuna_transport.services.demand.delivery_method_service import (
	canonical_delivery_method,
	delivery_methods_are_compatible,
)
from vuna_transport.services.fleet.vehicle_category_service import (
	CAPACITY_DIMENSIONS,
	evaluate_vehicle_category,
	get_available_vehicle_category_counts,
	get_capacity_breakdown,
	get_vehicle_categories,
	maximum_fitting_fraction,
	validate_vehicle_access_limit,
)
from vuna_transport.services.planning import customer_planning_source as source
from vuna_transport.services.planning.delivery_load_service import (
	build_delivery_load_from_allocations,
	get_capacity_buffer_percentage,
)
from vuna_transport.services.planning.dispatch_filter_service import (
	as_filter_conditions,
	validate_additional_filters,
	validate_configured_filters,
)
from vuna_transport.services.planning.load_validation_service import PLANNABLE_DELIVERY_ORDER_STATUSES
from vuna_transport.services.routing.geography_service import (
	calculate_delivery_window_compatibility,
	calculate_geographic_cohesion,
)
from vuna_transport.services.shared.measurement_service import get_transport_uoms
from vuna_transport.services.shared.security_service import (
	require_company_access,
	require_doctype_permission,
	require_document_permission,
)

PRIORITY_RANK = {"Urgent": 4, "High": 3, "Medium": 2, "Low": 1, None: 0, "": 0}
MAX_CANDIDATE_ORDERS = 100
MAX_GENERATED_CANDIDATES = 60
STRATEGIES = ("priority", "largest", "smallest")
logger = frappe.logger("vuna_transport.load_suggestions")


@frappe.whitelist()
def suggest_delivery_loads(
	company: str,
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
	delivery_date: str | None = None,
	delivery_order_names: list | str | None = None,
	option_count: int | None = None,
	delivery_date_from: str | None = None,
	delivery_date_to: str | None = None,
	planning_filters: dict | str | None = None,
	additional_filters: list | str | None = None,
):
	"""Return ranked load configurations without changing transactional state."""
	require_doctype_permission(source.doctype(), "read")
	require_doctype_permission("Transport Load", "create")
	require_company_access(company)
	settings = frappe.get_cached_doc("Transport Settings")
	requested_count = cint(option_count or settings.load_suggestion_option_count or 3)
	requested_count = max(3, min(requested_count, 10))
	delivery_order_names = _parse_names(delivery_order_names)
	configured_filters = validate_configured_filters(company, planning_filters)
	additional_filter_conditions = validate_additional_filters(additional_filters)

	orders = _get_plannable_orders(
		company,
		source_warehouse=source_warehouse,
		transport_zone=transport_zone,
		delivery_method=delivery_method,
		priority=priority,
		customer=customer,
		delivery_address=delivery_address,
		territory=territory,
		county=county,
		town=town,
		constituency=constituency,
		delivery_date_from=delivery_date_from or delivery_date,
		delivery_date_to=delivery_date_to or delivery_date,
		delivery_order_names=delivery_order_names,
		planning_filters=configured_filters,
		additional_filters=additional_filter_conditions,
	)
	logger.debug(
		"Load suggestion candidate pool: company=%s orders=%s selected=%s",
		company,
		len(orders),
		bool(delivery_order_names),
	)
	if not orders:
		return {
			"suggestions": [],
			"requested_option_count": requested_count,
			"warnings": [_("No unallocated Delivery Orders match the planning filters.")],
		}

	uoms = get_transport_uoms()
	load_uoms = {"weight_uom": uoms.weight_uom, "volume_uom": uoms.volume_uom}
	categories = get_vehicle_categories(load_uoms, get_capacity_buffer_percentage())
	if not categories:
		return {
			"suggestions": [],
			"requested_option_count": requested_count,
			"warnings": [_("Configure at least one active Vehicle Category.")],
		}

	candidates = []
	exclusions = []
	for pool in _partition_orders(orders).values():
		pool_dates = [getdate(order.required_delivery_date) for order in pool if order.required_delivery_date]
		first_date = min(pool_dates) if pool_dates else None
		last_date = max(pool_dates) if pool_dates else first_date
		availability = get_available_vehicle_category_counts(
			pool[0].company,
			first_date,
			from_time=f"{first_date} 00:00:00" if first_date else None,
			to_time=f"{add_days(last_date, 1)} 00:00:00" if last_date else None,
		)
		for category in categories:
			available_count = cint(availability["counts"].get(category.name))
			for strategy in STRATEGIES:
				candidate = _build_candidate(pool, category, strategy)
				if not candidate:
					continue
				candidate["available_vehicle_count"] = available_count
				candidate["fleet_availability_tracked"] = availability["fleet_tracked"]
				_score_candidate(candidate)
				candidates.append(candidate)
				if len(candidates) >= MAX_GENERATED_CANDIDATES:
					break
			if len(candidates) >= MAX_GENERATED_CANDIDATES:
				break

	candidates = _deduplicate_candidates(candidates)
	candidates.sort(
		key=lambda candidate: (
			-candidate["score"],
			-_bottleneck_utilization(candidate["utilization"]),
			candidate["vehicle_category"],
		)
	)
	selected = candidates[:requested_count]
	logger.debug(
		"Load suggestions generated: candidates=%s distinct=%s returned=%s",
		min(len(candidates), MAX_GENERATED_CANDIDATES),
		len(candidates),
		len(selected),
	)
	for index, candidate in enumerate(selected, 1):
		candidate["rank"] = index
		candidate["recommended"] = index == 1

	warnings = []
	if len(selected) < requested_count:
		warnings.append(
			_(
				"Only {0} distinct valid configuration(s) exist for the current filters; {1} were requested."
			).format(len(selected), requested_count)
		)
	return {
		"suggestions": selected,
		"requested_option_count": requested_count,
		"candidate_order_count": len(orders),
		"warnings": warnings,
		"excluded_reasons": list(dict.fromkeys(exclusions))[:10],
	}


@frappe.whitelist()
def create_delivery_load_from_suggestion(suggestion: dict | str):
	"""Revalidate browser selections and create one draft Transport Load atomically."""
	require_doctype_permission("Transport Load", "create")
	suggestion = frappe.parse_json(suggestion) if isinstance(suggestion, str) else suggestion
	if not isinstance(suggestion, dict):
		frappe.throw(_("Select a valid load suggestion."))
	vehicle_category = suggestion.get("vehicle_category")
	allocations = suggestion.get("allocations") or []
	if not vehicle_category or not allocations:
		frappe.throw(_("The suggestion does not contain a Vehicle Category and allocations."))
	if any(row.get("transport_demand") for row in allocations):
		load = build_delivery_load_from_allocations(
			allocations, vehicle_category, source_warehouse=suggestion.get("source_warehouse")
		)
		load.insert()
		load.add_comment("Info", _("Created from ranked customer Transport Demand suggestion."))
		return load.name

	delivery_order_names = sorted(
		{row.get("delivery_order") for row in allocations if row.get("delivery_order")}
	)
	delivery_orders = {name: frappe.get_doc("Delivery Order", name) for name in delivery_order_names}
	for delivery_order in delivery_orders.values():
		require_document_permission(delivery_order, "read")
	first = delivery_orders[delivery_order_names[0]]
	_validate_acceptance_context(delivery_orders.values(), first)

	load = build_delivery_load_from_allocations(allocations, vehicle_category)

	load.insert()
	load.add_comment(
		"Info",
		_("Created from ranked load suggestion using Vehicle Category {0}.").format(vehicle_category),
	)
	return load.name


def _get_plannable_orders(
	company,
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
	delivery_date_from=None,
	delivery_date_to=None,
	delivery_order_names=None,
	planning_filters=None,
	additional_filters=None,
):
	filters = {
		"company": company,
		"docstatus": 1,
		"status": ["in", PLANNABLE_DELIVERY_ORDER_STATUSES],
	}
	from vuna_transport.services.demand.delivery_order_service import get_open_sales_order_names

	filters["sales_order"] = ["in", get_open_sales_order_names(company)]
	if source_warehouse:
		filters["source_warehouse"] = source_warehouse
	if transport_zone:
		filters["transport_zone"] = transport_zone
	if delivery_method:
		filters["delivery_method"] = delivery_method
	if priority:
		filters["priority"] = priority
	if customer:
		filters["customer"] = customer
	if delivery_address:
		filters["delivery_address"] = delivery_address
	if territory:
		filters["territory"] = territory
	if county:
		filters["county"] = county
	if town:
		filters["town"] = town
	if constituency:
		filters["constituency"] = constituency
	if planning_filters:
		for fieldname in (
			"delivery_group",
			"transport_compatibility_group",
			"preferred_route",
			"maximum_vehicle_category",
			"transport_handling_profile",
		):
			if planning_filters.get(fieldname):
				filters[fieldname] = planning_filters[fieldname]
		delivery_address = delivery_address or planning_filters.get("delivery_address")
		territory = territory or planning_filters.get("territory")
		county = county or planning_filters.get("county")
		constituency = constituency or planning_filters.get("constituency")
		town = town or planning_filters.get("town")
	if delivery_address:
		filters["delivery_address"] = delivery_address
	if territory:
		filters["territory"] = territory
	if county:
		filters["county"] = county
	if town:
		filters["town"] = town
	if constituency:
		filters["constituency"] = constituency
	if delivery_date_from and delivery_date_to and getdate(delivery_date_from) > getdate(delivery_date_to):
		frappe.throw(_("Delivery Date From cannot be after Delivery Date To."))
	if delivery_date_from and delivery_date_to:
		filters["required_delivery_date"] = [
			"between",
			[getdate(delivery_date_from), getdate(delivery_date_to)],
		]
	elif delivery_date_from:
		filters["required_delivery_date"] = [">=", getdate(delivery_date_from)]
	elif delivery_date_to:
		filters["required_delivery_date"] = ["<=", getdate(delivery_date_to)]
	if delivery_order_names:
		filters["name"] = ["in", delivery_order_names]
	fields = [
		"name",
		"company",
		"source_warehouse",
		"required_delivery_date",
		"delivery_method",
		"transport_zone",
		"preferred_route",
		"transport_handling_profile",
		"transport_compatibility_group",
		"priority",
		"customer",
		"customer_name",
		"delivery_address",
		"territory",
		"county",
		"constituency",
		"town",
		"destination_latitude",
		"destination_longitude",
		"delivery_from_time",
		"delivery_to_time",
		"weight_uom",
		"volume_uom",
		"refrigeration_required",
		"tail_lift_required",
		"required_body_type",
		"special_equipment_required",
		"maximum_vehicle_category",
		"restricted_access",
		"average_service_duration",
	]
	warehouse_options = {}
	if source.doctype() == "Transport Demand" and source_warehouse:
		filters.pop("source_warehouse", None)
		warehouse_options["or_filters"] = [
			["source_warehouse", "=", source_warehouse],
			["source_warehouse", "is", "not set"],
		]
	orders = frappe.get_list(
		source.doctype(),
		filters=as_filter_conditions(filters, additional_filters),
		fields=source.fields(fields),
		order_by=f"priority desc, {source.field('required_delivery_date')} asc, creation asc",
		limit_page_length=MAX_CANDIDATE_ORDERS,
		**warehouse_options,
	)
	if delivery_order_names and len(orders) != len(set(delivery_order_names)):
		frappe.throw(_("One or more selected Delivery Orders are unavailable or not permitted."))
	_populate_delivery_method_workflows(orders)
	_populate_available_items(orders, source_warehouse=source_warehouse)
	if source.doctype() == "Transport Demand" and source_warehouse:
		for order in orders:
			order.source_warehouse = source_warehouse
	return [
		order
		for order in orders
		if order.order_items and not order.delivery_method_disabled and order.requires_transport_planning
	]


def _get_address_names(delivery_address=None, territory=None, county=None, constituency=None, town=None):
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


def _populate_delivery_method_workflows(orders):
	methods = sorted({order.delivery_method for order in orders if order.delivery_method})
	method_rows = (
		frappe.get_all(
			"Delivery Method",
			filters={"name": ["in", methods]},
			fields=["name", "requires_transport_planning", "disabled"],
		)
		if methods
		else []
	)
	planning_methods = {
		canonical_delivery_method(row.name) for row in method_rows if row.requires_transport_planning
	}
	disabled_methods = {canonical_delivery_method(row.name) for row in method_rows if row.disabled}
	for order in orders:
		order.delivery_method = canonical_delivery_method(order.delivery_method)
		order.delivery_method_disabled = order.delivery_method in disabled_methods
		order.requires_transport_planning = (
			not order.delivery_method or order.delivery_method in planning_methods
		)


def _populate_available_items(orders, source_warehouse=None):
	order_map = {order.name: order for order in orders}
	if source.doctype() == "Transport Demand":
		metrics = source.metrics(list(order_map), include_items=True, source_warehouse=source_warehouse)
		for order in orders:
			values = metrics[order.name]
			order.order_items = []
			order.totals = {d: values[d] for d in CAPACITY_DIMENSIONS}
			for row in values["items"]:
				if row["available_quantity"] <= 0:
					continue
				item = frappe._dict(row)
				item.transport_qty = item.transport_quantity
				item.metrics = {d: item[d] for d in CAPACITY_DIMENSIONS}
				order.order_items.append(item)
		return
	items = frappe.get_all(
		"Delivery Order Item",
		filters={"parent": ["in", list(order_map)]},
		fields=[
			"name",
			"parent",
			"transport_qty",
			"weight",
			"volume",
			"pallets",
			"cases",
			"crates",
			"weight_uom",
			"volume_uom",
		],
		order_by="parent asc, idx asc",
	)
	active_loads = frappe.get_all("Transport Load", filters={"docstatus": ["in", [0, 1]]}, pluck="name")
	allocated_by_item = defaultdict(float)
	if active_loads:
		allocation_rows = frappe.get_all(
			"Transport Load Item",
			filters={
				"parent": ["in", active_loads],
				"delivery_order": ["in", list(order_map)],
			},
			fields=["delivery_order_item", "allocated_quantity"],
		)
		for row in allocation_rows:
			allocated_by_item[row.delivery_order_item] += flt(row.allocated_quantity)

	for order in orders:
		order.order_items = []
		order.totals = _empty_totals()
	for item in items:
		available = max(flt(item.transport_qty) - allocated_by_item[item.name], 0)
		if available <= 0 or flt(item.transport_qty) <= 0:
			continue
		ratio = available / flt(item.transport_qty)
		metrics = {
			"weight": flt(item.weight) * ratio,
			"volume": flt(item.volume) * ratio,
			"pallets": flt(item.pallets) * ratio,
			"cases": flt(item.cases) * ratio,
			"crates": flt(item.crates) * ratio,
		}
		item.available_quantity = available
		item.metrics = metrics
		order_map[item.parent].order_items.append(item)
		order_map[item.parent].totals = _add_totals(order_map[item.parent].totals, metrics)


def _partition_orders(orders):
	pools = defaultdict(list)
	for order in orders:
		key = (
			order.company,
			canonical_delivery_method(order.delivery_method) or "",
			(order.transport_compatibility_group or "").strip().lower(),
			order.transport_zone or "",
			bool(order.refrigeration_required),
			bool(order.tail_lift_required),
			order.required_body_type or "",
			(order.special_equipment_required or "").strip().lower(),
		)
		pools[key].append(order)
	return pools


def _build_candidate(pool, category, strategy):
	orders = _sort_orders(pool, strategy)
	totals = _empty_totals()
	allocations = []
	included_orders = []
	requirements = {
		"refrigeration_required": bool(
			pool[0].refrigeration_required or _requires_refrigeration(pool[0].transport_handling_profile)
		),
		"tail_lift_required": bool(
			pool[0].tail_lift_required or _requires_tail_lift(pool[0].transport_handling_profile)
		),
		"required_body_type": pool[0].required_body_type,
		"special_equipment_required": pool[0].special_equipment_required,
	}
	capability = evaluate_vehicle_category(totals, category, requirements)
	if not capability["compatible"]:
		return None
	if any(
		not validate_vehicle_access_limit(category, order.maximum_vehicle_category)["compatible"]
		for order in pool
	):
		return None

	for order in orders:
		combined = _add_totals(totals, order.totals)
		if evaluate_vehicle_category(combined, category, requirements)["compatible"]:
			fraction = 1
		elif not allocations:
			fraction = maximum_fitting_fraction(order.totals, category)
			if fraction <= 0:
				continue
		else:
			continue

		for item in order.order_items:
			quantity = round(flt(item.available_quantity) * fraction, 6)
			if quantity <= 0:
				continue
			allocations.append(
				{
					source.allocation_fields()[0]: order.name,
					source.allocation_fields()[1]: item.name,
					"allocated_quantity": quantity,
					"available_quantity": item.available_quantity,
					"partial": fraction < 0.999999,
				}
			)
		totals = _add_totals(totals, _scale_totals(order.totals, fraction))
		included_orders.append(order)

	if not allocations:
		return None
	result = evaluate_vehicle_category(totals, category, requirements)
	if not result["compatible"]:
		return None
	source_warehouses = sorted(
		{order.source_warehouse for order in included_orders if order.source_warehouse}
	)
	delivery_dates = sorted(
		{
			str(getdate(order.required_delivery_date))
			for order in included_orders
			if order.required_delivery_date
		}
	)
	return {
		"vehicle_category": category.name,
		"vehicle_category_label": category.vehicle_category_name or category.name,
		"capacity_override_percentage": flt(getattr(category, "capacity_override_percentage", 0)),
		"capacity_override_applied": bool(getattr(category, "capacity_override_applied", False)),
		"capacity_override_allowed": bool(getattr(category, "capacity_override_allowed", False)),
		"capacity": get_capacity_breakdown(totals, category),
		"company": pool[0].company,
		"source_warehouse": source_warehouses[0] if len(source_warehouses) == 1 else None,
		"source_warehouses": source_warehouses,
		"delivery_date": delivery_dates[0] if len(delivery_dates) == 1 else None,
		"delivery_dates": delivery_dates,
		"delivery_method": canonical_delivery_method(pool[0].delivery_method),
		"transport_zone": pool[0].transport_zone,
		"transport_route": pool[0].preferred_route,
		"strategy": strategy,
		"delivery_orders": [
			{
				"name": order.name,
				"customer": order.customer,
				"customer_name": order.customer_name,
				"delivery_address": order.delivery_address,
			}
			for order in included_orders
		],
		"allocations": allocations,
		"totals": {dimension: round(flt(totals[dimension]), 6) for dimension in CAPACITY_DIMENSIONS},
		"utilization": result["utilization"],
		"requirements": requirements,
		"_orders": included_orders,
	}


def _score_candidate(candidate):
	orders = candidate.pop("_orders")
	geography = calculate_geographic_cohesion(orders)
	windows = calculate_delivery_window_compatibility(orders)
	zones = {order.transport_zone for order in orders if order.transport_zone}
	routes = {order.preferred_route for order in orders if order.preferred_route}
	priorities = {order.priority for order in orders if order.priority}
	zone_score = 100 if len(zones) == 1 else 60
	route_score = 100 if len(routes) == 1 else 60 if not routes else 40
	priority_score = 100 if len(priorities) <= 1 else 75
	stop_count = len({order.delivery_address for order in orders if order.delivery_address})
	stop_score = max(40, 100 - max(stop_count - 5, 0) * 10)
	utilization_score = _bottleneck_utilization(candidate["utilization"])
	score = (
		geography["score"] * 0.30
		+ utilization_score * 0.25
		+ zone_score * 0.15
		+ route_score * 0.10
		+ windows["score"] * 0.10
		+ priority_score * 0.05
		+ stop_score * 0.05
	)

	reasons = [
		_("Same company and compatible fulfilment requirements."),
		_("{0}% bottleneck capacity utilization.").format(round(utilization_score, 1)),
	]
	warnings = []
	if len(zones) == 1:
		reasons.append(_("All destinations are in the same Transport Zone."))
	if len(routes) == 1:
		reasons.append(_("All configured orders share the same preferred route."))
	if geography["average_distance_km"] is not None:
		reasons.append(
			_("Average destination separation is {0} km.").format(geography["average_distance_km"])
		)
	if geography["missing_coordinate_count"]:
		warnings.append(
			_(
				"{0} order(s) have no destination coordinates; the geographic score uses zone fallback."
			).format(geography["missing_coordinate_count"])
		)
	if windows["overlap"] is False:
		warnings.append(_("Configured delivery windows do not overlap."))
	elif windows["overlap"]:
		reasons.append(_("Configured delivery windows are compatible."))
	if any(row["partial"] for row in candidate["allocations"]):
		warnings.append(_("This configuration partially allocates at least one Delivery Order."))
	if not any(flt(candidate["totals"][dimension]) for dimension in CAPACITY_DIMENSIONS):
		warnings.append(
			_(
				"No weight, volume, pallet, case or crate values are available; capacity utilization cannot influence this ranking."
			)
		)
	if candidate["fleet_availability_tracked"]:
		if candidate["available_vehicle_count"] > 0:
			reasons.append(
				_("{0} owned physical vehicle(s) currently available in this category.").format(
					candidate["available_vehicle_count"]
				)
			)
		else:
			warnings.append(
				_(
					"No owned physical vehicle is currently available in this category; "
					"subcontracted capacity may be selected during Vehicle Allocation."
				)
			)
	else:
		warnings.append(
			_("Physical fleet availability is not configured; capacity compatibility only was scored.")
		)
	if not candidate["delivery_method"]:
		warnings.append(_("Delivery Method is not configured on these orders."))
	if candidate["requirements"].get("refrigeration_required"):
		reasons.append(_("The Vehicle Category satisfies the refrigeration requirement."))
	if candidate["requirements"].get("tail_lift_required"):
		reasons.append(_("The Vehicle Category has the required tail lift."))
	if candidate["requirements"].get("required_body_type"):
		reasons.append(
			_("The Vehicle Category has the required {0} body type.").format(
				candidate["requirements"]["required_body_type"]
			)
		)
	candidate["score"] = round(max(0, min(score, 100)), 2)
	candidate["geography"] = geography
	candidate["delivery_windows"] = windows
	candidate["total_stops"] = stop_count
	candidate["positive_factors"] = reasons
	candidate["warnings"] = warnings


def _validate_acceptance_context(delivery_orders, first):
	for order in delivery_orders:
		if order.docstatus != 1 or order.status not in PLANNABLE_DELIVERY_ORDER_STATUSES:
			frappe.throw(
				_("Delivery Order {0} is no longer available. Refresh load suggestions.").format(order.name)
			)
		comparisons = (
			("company", "company"),
			("transport_zone", "Transport Zone"),
			("transport_handling_profile", "handling profile"),
			("transport_compatibility_group", "Transport Compatibility Group"),
			("refrigeration_required", "refrigeration requirement"),
			("tail_lift_required", "tail-lift requirement"),
			("required_body_type", "vehicle body requirement"),
			("special_equipment_required", "special-equipment requirement"),
		)
		for fieldname, label in comparisons:
			if (order.get(fieldname) or None) != (first.get(fieldname) or None):
				frappe.throw(_("Delivery Order {0} has an incompatible {1}.").format(order.name, label))
		if not delivery_methods_are_compatible(order.delivery_method, first.delivery_method):
			frappe.throw(_("Delivery Order {0} has an incompatible Delivery Method.").format(order.name))


def _sort_orders(orders, strategy):
	if strategy == "smallest":
		return sorted(
			orders, key=lambda order: (_order_size(order), -PRIORITY_RANK.get(order.priority, 0), order.name)
		)
	if strategy == "largest":
		return sorted(
			orders, key=lambda order: (-_order_size(order), -PRIORITY_RANK.get(order.priority, 0), order.name)
		)
	return sorted(
		orders, key=lambda order: (-PRIORITY_RANK.get(order.priority, 0), -_order_size(order), order.name)
	)


def _order_size(order):
	return flt(order.totals["weight"]) + flt(order.totals["volume"])


def _empty_totals():
	return {dimension: 0.0 for dimension in CAPACITY_DIMENSIONS}


def _add_totals(first, second):
	return {
		dimension: flt(first.get(dimension)) + flt(second.get(dimension)) for dimension in CAPACITY_DIMENSIONS
	}


def _scale_totals(totals, fraction):
	return {dimension: flt(totals.get(dimension)) * fraction for dimension in CAPACITY_DIMENSIONS}


def _bottleneck_utilization(utilization):
	configured = [flt(value) for value in utilization.values() if value is not None]
	return min(max(configured), 100) if configured else 0


def _deduplicate_candidates(candidates):
	unique = {}
	for candidate in candidates:
		signature = (
			candidate["vehicle_category"],
			tuple(
				sorted(
					(
						row.get("transport_demand_item") or row.get("delivery_order_item"),
						round(flt(row["allocated_quantity"]), 6),
					)
					for row in candidate["allocations"]
				)
			),
		)
		if signature not in unique or candidate["score"] > unique[signature]["score"]:
			unique[signature] = candidate
	return list(unique.values())


def _parse_names(names):
	if not names:
		return []
	if isinstance(names, str):
		names = frappe.parse_json(names)
	return list(dict.fromkeys(names))


def _requires_refrigeration(profile):
	profile = (profile or "").lower()
	return any(keyword in profile for keyword in ("refriger", "cold chain", "chilled", "frozen"))


def _requires_tail_lift(profile):
	return "tail lift" in (profile or "").lower()
