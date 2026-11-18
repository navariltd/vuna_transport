"""Permission-checked, on-demand road previews of saved operational routes."""

import math

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, escape_html

from vuna_transport.services.routing.google_routes_service import MAX_POINTS, compute_route
from vuna_transport.services.routing.location_service import (
	get_address_planning_data,
	get_warehouse_planning_data,
)
from vuna_transport.services.shared.security_service import require_authenticated, require_document_permission

ROUTING_ROLES = {"System Manager", "Transport Manager", "Transport Dispatcher", "Transport Planner"}
SUPPORTED_DOCUMENTS = {"Transport Load", "Delivery Trip"}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=10, seconds=60, user_based=True)
def preview_road_route(doctype: str, name: str, return_to_start: int = 0):
	"""A live preview only: no save, reordering, stock posting or costing mutation."""
	require_authenticated()
	if doctype not in SUPPORTED_DOCUMENTS or not (set(frappe.get_roles()) & ROUTING_ROLES):
		frappe.throw(
			_("Road route previews require a transport planning role and a supported document."),
			frappe.PermissionError,
		)
	doc = frappe.get_doc(doctype, name)
	require_document_permission(doc, "write")
	if doc.docstatus == 2:
		frappe.throw(_("Cancelled transport records cannot request road routes."))
	settings = frappe.get_cached_doc("Transport Settings")
	if not settings.enabled or settings.get("routing_provider") != "Google":
		frappe.throw(_("Select Google as the Routing Provider in Transport Settings to preview road routes."))
	if cint(return_to_start) not in (0, 1):
		frappe.throw(_("Choose whether the route returns to its start."))
	if doctype == "Delivery Trip":
		if not doc.driver_address:
			frappe.throw(_("Set the trip's Driver Address before previewing its road route."))
		origin = _address_point(doc.driver_address, doc.company)
	elif doc.movement_type == "Supplier Pickup":
		origin = _address_point(doc.source_address, doc.company)
	else:
		origin = _warehouse_point(doc.source_warehouse, doc.company)
	stops = _trip_stops(doc) if doctype == "Delivery Trip" else _load_stops(doc)
	if not stops:
		frappe.throw(_("Add at least one destination before previewing a road route."))
	points = [origin, *stops]
	if cint(return_to_start):
		points.append(origin)
	if len(points) > MAX_POINTS:
		frappe.throw(
			_("This route exceeds Google's 25 intermediate-waypoint limit. Split it into smaller routes.")
		)
	key = settings.get_password("google_routes_api_key", raise_exception=False)
	if not key:
		key = frappe.get_cached_doc("Google Settings").get("api_key")
	if not key:
		frappe.throw(_("Set a Google Routes API Key in Transport Settings or an API Key in Google Settings."))
	result = compute_route(points, key)
	for index, leg in enumerate(result["legs"]):
		leg.update(from_label=points[index]["label"], to_label=points[index + 1]["label"])
	result.update(
		provider="Google",
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
	if doctype == "Transport Load":
		result["warnings"].append(
			_(
				"Load destinations follow their first occurrence; the Delivery Trip determines the actual visit order."
			)
		)
	return result


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
		stops.append(
			_warehouse_point(name, doc.company) if is_warehouse else _address_point(name, doc.company)
		)
	return stops


def _trip_stops(doc):
	# Never sort or deduplicate actual visits: a repeat address can be a later pickup.
	return [_address_point(stop.address, doc.company) for stop in doc.delivery_stops]
