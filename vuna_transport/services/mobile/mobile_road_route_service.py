"""On-demand, assigned-trip geometry. Never persist provider geometry or replan a trip."""

import frappe
from frappe import _
from frappe.utils import cint

from vuna_transport.services.execution.lineage_service import get_delivery_trip_loads
from vuna_transport.services.mobile.mobile_device_service import _get_assigned_trip, _require_mobile_driver
from vuna_transport.services.mobile.mobile_map_service import get_mobile_map_configuration
from vuna_transport.services.routing.google_routes_service import compute_ordered_route, split_route_points
from vuna_transport.services.routing.location_service import (
	get_address_planning_data,
	get_warehouse_planning_data,
)
from vuna_transport.services.routing.road_routing_service import _point
from vuna_transport.services.routing.routing_policy_service import (
	get_limit,
	reserve_usage,
)


def get_trip_road_route(installation_id, trip_name, device_identifier=None):
	# Device authorization and assignment precede settings, locations and provider access.
	driver = _require_mobile_driver(installation_id, device_identifier)
	trip = _get_assigned_trip(trip_name, driver.name)
	settings = frappe.get_cached_doc("Transport Settings")
	# This is a display permission for assigned drivers, not a planning/optimization role.
	if not cint(settings.get("allow_driver_road_routes")):
		frappe.throw(_("Driver road routes are disabled in Transport Settings."), frappe.PermissionError)
	if (
		not settings.enabled
		or settings.get("routing_provider") != "Google"
		or get_mobile_map_configuration(settings)["provider"] != "google"
	):
		frappe.throw(_("Google road-route display is not enabled for this mobile app."))
	stops = sorted(trip.delivery_stops, key=lambda row: cint(row.idx))
	if not stops or len(stops) > get_limit(settings, "routing_max_locations"):
		frappe.throw(_("The trip must have stops within the configured Maximum Stops per Route."))
	origin = _trip_origin(trip)
	# Keep repeated/co-located visits. Never skip an unresolved stop or optimize order.
	points = [origin, *[_address_point(stop.address) for stop in stops]]
	return_to_start = bool(cint(trip.get("vuna_route_return_to_start")))
	if return_to_start:
		points.append(origin)
	key = settings.get_password("google_routes_api_key", raise_exception=False)
	if not key:
		key = frappe.get_cached_doc("Google Settings").get("api_key")
	if not key:
		frappe.throw(_("Ask your administrator to configure the Google Routes API key."))
	reserve_usage(settings, requests=len(split_route_points(points)), mobile=True)
	result = compute_ordered_route(points, key, include_geometry=True)
	return {
		"version": 1,
		"provider": "google",
		"trip_name": trip.name,
		"stop_order": [stop.name for stop in stops],
		"return_to_start": return_to_start,
		"origin": {key: origin[key] for key in ("latitude", "longitude")},
		"encoded_polylines": result["encoded_polylines"],
		"attribution": "Google Maps",
	}


def _address_point(name):
	if not name:
		frappe.throw(_("Every trip endpoint needs an Address with exact coordinates."))
	data = get_address_planning_data(name)
	point = _point(data.get("latitude"), data.get("longitude"), name)
	if point["latitude"] == point["longitude"] == 0:
		frappe.throw(_("Replace placeholder coordinates (0, 0) before displaying a road route."))
	return point


def _trip_origin(trip):
	loads = get_delivery_trip_loads(trip)
	if not loads:
		return _address_point(trip.get("driver_address"))
	origins = {load.source_warehouse for load in loads}
	if any(load.company != trip.company for load in loads) or len(origins) != 1 or not next(iter(origins)):
		frappe.throw(_("Trip road routing needs one common loading warehouse in the trip company."))
	name = next(iter(origins))
	warehouse = frappe.get_doc("Warehouse", name)
	if warehouse.company != trip.company or warehouse.disabled or warehouse.is_group:
		frappe.throw(_("The trip loading warehouse must be active and in the trip company."))
	return _address_point(get_warehouse_planning_data(name).get("address"))
