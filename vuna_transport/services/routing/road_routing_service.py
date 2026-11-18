"""Permission-checked, on-demand road previews of saved operational routes."""

import math

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, escape_html

from vuna_transport.services.routing import allocation_route_service as allocation_routes
from vuna_transport.services.routing.google_optimization_service import optimize_route
from vuna_transport.services.routing.google_routes_service import compute_ordered_route, split_route_points
from vuna_transport.services.routing.location_service import (
	get_address_planning_data,
	get_warehouse_planning_data,
)
from vuna_transport.services.routing.routing_policy_service import (
	get_limit,
	require_routing_role,
	reserve_usage,
)
from vuna_transport.services.shared.security_service import require_authenticated, require_document_permission

SUPPORTED_DOCUMENTS = {"Transport Load", "Vehicle Allocation", "Delivery Trip"}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=10, seconds=60, user_based=True)
def preview_road_route(doctype: str, name: str, return_to_start: int = 0, optimize_stops: int = 0):
	"""A live preview only: no save, reordering, stock posting or costing mutation."""
	require_authenticated()
	if doctype not in SUPPORTED_DOCUMENTS:
		frappe.throw(
			_("Road route previews require a transport planning role and a supported document."),
			frappe.PermissionError,
		)
	settings = frappe.get_cached_doc("Transport Settings")
	require_routing_role(settings)
	doc = frappe.get_doc(doctype, name)
	require_document_permission(doc, "write")
	if doc.docstatus == 2:
		frappe.throw(_("Cancelled transport records cannot request road routes."))
	if not settings.enabled or settings.get("routing_provider") != "Google":
		frappe.throw(_("Select Google as the Routing Provider in Transport Settings to preview road routes."))
	if cint(return_to_start) not in (0, 1):
		frappe.throw(_("Choose whether the route returns to its start."))
	if cint(optimize_stops) not in (0, 1):
		frappe.throw(_("Choose whether to optimize the stop order."))
	if cint(optimize_stops):
		if not settings.get("enable_google_optimization"):
			frappe.throw(
				_("Enable Google Route Optimization and configure its service account in Transport Settings.")
			)
		_validate_optimization(doc, doctype)
	manifest = None
	if doctype == "Vehicle Allocation":
		manifest = allocation_routes.build_manifest(doc, return_to_start=bool(cint(return_to_start)))
		origin = _warehouse_point(manifest["source_warehouse"], doc.company)
		operational_stops = manifest["stops"]
		if doc.get("route_stops"):
			operational_stops = allocation_routes.ordered_stops(doc, manifest)
		stops = []
		for stop in operational_stops:
			point = _address_point(stop["address"], doc.company)
			point["stop_key"] = stop["stop_key"]
			stops.append(point)
	elif doctype == "Delivery Trip":
		from vuna_transport.services.execution.lineage_service import get_delivery_trip_loads

		loads = get_delivery_trip_loads(doc)
		for load in loads:
			require_document_permission(load, "read")
		origins = {load.source_warehouse for load in loads}
		if loads and (len(origins) != 1 or not next(iter(origins))):
			frappe.throw(_("Trip road routing needs one common loading warehouse."))
		origin = (
			_warehouse_point(next(iter(origins)), doc.company)
			if loads
			else _address_point(doc.driver_address, doc.company)
		)
		stops = _trip_stops(doc)
	elif doc.movement_type == "Supplier Pickup":
		origin = _address_point(doc.source_address, doc.company)
	else:
		origin = _warehouse_point(doc.source_warehouse, doc.company)
	if doctype == "Transport Load":
		stops = _load_stops(doc)
	if not stops:
		frappe.throw(_("Add at least one destination before previewing a road route."))
	points = [origin, *stops]
	if cint(return_to_start):
		points.append(origin)
	if len(stops) > get_limit(settings, "routing_max_locations"):
		frappe.throw(_("This route exceeds the Maximum Routing Locations configured in Transport Settings."))
	if cint(optimize_stops) and len(stops) < 2:
		frappe.throw(_("Stop optimization needs at least two distinct delivery locations."))
	if cint(optimize_stops):
		result = optimize_route(origin, stops, settings, return_to_start=bool(cint(return_to_start)))
		ordered_stops = [stops[index] for index in result.pop("stop_order")]
		ordered_points = [origin, *ordered_stops, *([origin] if cint(return_to_start) else [])]
	else:
		key = settings.get_password("google_routes_api_key", raise_exception=False)
		if not key:
			key = frappe.get_cached_doc("Google Settings").get("api_key")
		if not key:
			frappe.throw(
				_("Set a Google Routes API Key in Transport Settings or an API Key in Google Settings.")
			)
		reserve_usage(settings, requests=len(split_route_points(points)))
		result = compute_ordered_route(points, key)
		ordered_points = points
	for index, leg in enumerate(result["legs"]):
		leg.update(from_label=ordered_points[index]["label"], to_label=ordered_points[index + 1]["label"])
	result.update(
		provider="Google",
		return_to_start=cint(return_to_start),
		attribution="Google Maps",
		source_doctype=doctype,
		source_name=doc.name,
		warnings=[
			_("Preview only. Saved estimates, costs, stop order and execution records are unchanged."),
			_("Driving time excludes traffic and stop service time; this is not a delivery ETA."),
			_(
				"Standard driving directions do not verify truck dimensions, vehicle access restrictions or pickup dependencies."
			),
		],
	)
	if cint(optimize_stops):
		ordered_stops = ordered_points[1:-1] if cint(return_to_start) else ordered_points[1:]
		if doctype == "Transport Load":
			result["load_row_order"] = _load_row_order(doc, ordered_stops)
		elif doctype == "Delivery Trip":
			result["trip_stop_order"] = [point["stop_key"] for point in ordered_stops]
		result["source_modified"] = str(doc.modified)
		result["warnings"].extend(
			[
				_("Suggested order only. Apply it to the draft form, then save to keep the sequence."),
				_(
					"Google Route Optimization selected the full journey order. A return leg is included only when requested."
				),
			]
		)
	if doctype == "Transport Load" and not cint(optimize_stops):
		result["warnings"].append(
			_(
				"Load destinations follow their first occurrence; the Delivery Trip determines the actual visit order."
			)
		)
	if manifest:
		by_key = {row["stop_key"]: row for row in manifest["stops"]}
		ordered = ordered_points[1:-1] if cint(return_to_start) else ordered_points[1:]
		result.update(
			allocation_route_rows=allocation_routes.route_rows(
				[by_key[point["stop_key"]] for point in ordered]
			),
			route_fingerprint=manifest["fingerprint"],
			source_warehouse=manifest["source_warehouse"],
			source_modified=str(doc.modified),
			return_to_start=cint(return_to_start),
		)
		if doc.docstatus == 0:
			from vuna_transport.services.routing.route_preview_receipt import issue_receipt

			result["route_preview_token"] = issue_receipt(
				doc,
				manifest["fingerprint"],
				[point["stop_key"] for point in ordered],
				result["distance_meters"],
			)
		if settings.get("enable_transport_costing") and doc.vehicle:
			from vuna_transport.services.costing.transport_cost_estimation_service import (
				estimate_transport_journey,
			)

			result["cost_estimate"] = estimate_transport_journey(
				manifest["loads"],
				doc.vehicle,
				distance=result["distance_meters"] / 1000,
				distance_source="Routing Provider",
				as_of_date=doc.planned_reporting_time,
			)
	return result


def _validate_optimization(doc, doctype):
	if doctype in ("Vehicle Allocation", "Delivery Trip"):
		if (
			doc.docstatus != 0
			or doc.get("vuna_actual_start")
			or any(row.get("visited") for row in doc.get("delivery_stops") or [])
		):
			frappe.throw(_("Optimize the route before submitting or starting execution."))
		if doctype == "Vehicle Allocation":
			loads = allocation_routes.build_manifest(doc)["loads"]
		else:
			from vuna_transport.services.execution.lineage_service import get_delivery_trip_loads

			loads = get_delivery_trip_loads(doc)
			if not loads or any(not row.get("vuna_delivery_load") for row in doc.delivery_stops):
				frappe.throw(_("Trip optimization requires customer/depot stops linked to Transport Loads."))
			from vuna_transport.services.execution.trip_task_service import plan_tasks

			specs = plan_tasks(loads) if doc.get("vuna_task_based") else []
			keys = {
				spec["task_key"]
				for spec in specs
				if spec["task_type"] in ("Customer Delivery", "Transfer Receipt")
			}
			if len(keys) != len(specs) or any(
				key not in keys
				for stop in doc.delivery_stops
				for key in frappe.parse_json(stop.get("vuna_task_keys") or "[]")
			):
				frappe.throw(
					_("Pickup and diversion tasks need dependency-aware planning; review this trip manually.")
				)
		for load in loads:
			require_document_permission(load, "read")
			if load.company != doc.company or load.movement_type not in (
				"Customer Delivery",
				"Warehouse Transfer",
			):
				frappe.throw(_("Pickup-dependent journeys need manual stop planning."))
			_validate_load_constraints(load)
		return
	if (
		doctype != "Transport Load"
		or doc.docstatus != 0
		or doc.movement_type not in ("Customer Delivery", "Warehouse Transfer")
		or doc.get("active_vehicle_allocation")
		or doc.get("delivery_trip")
	):
		frappe.throw(
			_("Stop optimization is available only for draft customer/depot loads before allocation.")
		)
	_validate_load_constraints(doc)


def _validate_load_constraints(doc):
	# Routing does not model delivery windows, maximum vehicle access or
	# pickup-before-delivery dependencies. Never silently override those rules.
	if doc.get("preferred_route") or doc.get("transport_route"):
		frappe.throw(_("This load has a planned route. Review its stop order manually."))
	constraint_fields = (
		"delivery_from_time",
		"delivery_to_time",
		"maximum_vehicle_category",
		"restricted_access",
		"preferred_route",
	)
	for row in doc.delivery_orders:
		if row.get("transport_demand"):
			demand = frappe.get_doc("Transport Demand", row.transport_demand)
			require_document_permission(demand, "read")
			if demand.company != doc.company or demand.source_document_type == "Return Request":
				frappe.throw(
					_("Return pickups and cross-company demands cannot use automatic stop ordering.")
				)
			if any(demand.get(field) for field in constraint_fields):
				frappe.throw(
					_(
						"This demand has delivery windows, access restrictions or a planned route. Plan its stop order manually."
					)
				)
		if doc.movement_type == "Warehouse Transfer":
			if not row.destination_warehouse:
				continue
			warehouse = frappe.get_doc("Warehouse", row.destination_warehouse)
			require_document_permission(warehouse, "read")
			data = get_warehouse_planning_data(row.destination_warehouse)
			address = data.get("address")
		else:
			address = row.delivery_address
		if not address:
			continue
		address_doc = frappe.get_doc("Address", address)
		require_document_permission(address_doc, "read")
		data = get_address_planning_data(address)
		if any(data.get(field) for field in constraint_fields):
			frappe.throw(
				_("This load has delivery windows or access restrictions. Plan its stop order manually.")
			)


def _load_row_order(doc, ordered_stops):
	field = "destination_warehouse" if doc.movement_type == "Warehouse Transfer" else "delivery_address"
	groups = {}
	for row in doc.delivery_orders:
		groups.setdefault(row.get(field), []).append(row.name)
	return [name for point in ordered_stops for name in groups[point["stop_key"]]]


def _address_point(name, company):
	if not name:
		frappe.throw(_("Configure an Address with exact coordinates for every route endpoint."))
	address = frappe.get_doc("Address", name)
	require_document_permission(address, "read")
	for link in address.links:
		if link.link_doctype == "Company" and link.link_name != company:
			frappe.throw(_("The route address belongs to a different company."), frappe.PermissionError)
		if link.link_doctype == "Warehouse":
			warehouse = frappe.get_doc("Warehouse", link.link_name)
			require_document_permission(warehouse, "read")
			if warehouse.company != company:
				frappe.throw(
					_("The route address belongs to a different company's warehouse."), frappe.PermissionError
				)
	data = get_address_planning_data(name)
	point = _point(data.get("latitude"), data.get("longitude"), address.address_title or address.name)
	if point["latitude"] == 0 and point["longitude"] == 0:
		frappe.throw(
			_(
				"Address {0} has placeholder coordinates (0, 0). Set its exact location before previewing a road route."
			).format(escape_html(name))
		)
	return point


def _warehouse_point(name, company):
	if not name:
		frappe.throw(_("Select a Source Warehouse on the Transport Load before previewing the route."))
	warehouse = frappe.get_doc("Warehouse", name)
	require_document_permission(warehouse, "read")
	if warehouse.company != company or warehouse.disabled or warehouse.is_group:
		frappe.throw(_("Route warehouses must be active, non-group warehouses in the transport company."))
	data = get_warehouse_planning_data(name)
	point = _address_point(data.get("address"), company)
	point["label"] = name
	return point


def _point(latitude, longitude, label):
	try:
		if (
			latitude in (None, "")
			or longitude in (None, "")
			or isinstance(latitude, bool)
			or isinstance(longitude, bool)
		):
			raise ValueError
		latitude, longitude = float(latitude), float(longitude)
		if (
			not math.isfinite(latitude)
			or not math.isfinite(longitude)
			or not -90 <= latitude <= 90
			or not -180 <= longitude <= 180
		):
			raise ValueError
		return {"latitude": latitude, "longitude": longitude, "label": label}
	except (TypeError, ValueError, OverflowError):
		pass
	frappe.throw(
		_(
			"Set exact latitude and longitude on route address {0}; town or county reference points are not road-routing endpoints."
		).format(escape_html(label))
	)


def _load_stops(doc):
	stops, seen = [], set()
	for row in doc.delivery_orders:
		is_warehouse = doc.movement_type in ("Supplier Pickup", "Warehouse Transfer")
		name = row.destination_warehouse if is_warehouse else row.delivery_address
		key = (is_warehouse, name)
		if key in seen:
			continue
		seen.add(key)
		point = _warehouse_point(name, doc.company) if is_warehouse else _address_point(name, doc.company)
		point["stop_key"] = name
		stops.append(point)
	return stops


def _trip_stops(doc):
	# Never sort or deduplicate actual visits: a repeat address can be a later pickup.
	return [
		{**_address_point(stop.address, doc.company), "stop_key": stop.name} for stop in doc.delivery_stops
	]
