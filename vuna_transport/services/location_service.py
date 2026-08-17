"""Resolve operational locations and snapshot them onto planning documents."""

import frappe
from frappe.contacts.doctype.address.address import get_default_address


def get_address_planning_data(address_name):
	if not address_name or not frappe.db.exists("Address", address_name):
		return frappe._dict()

	address = frappe.get_cached_doc("Address", address_name)
	location = getattr(address, "vuna_location", None)
	latitude, longitude = get_point_coordinates(location)
	return frappe._dict(
		address=address.name,
		delivery_zone=getattr(address, "vuna_delivery_zone", None),
		location=location,
		latitude=latitude,
		longitude=longitude,
		delivery_from_time=getattr(address, "vuna_preferred_delivery_from", None),
		delivery_to_time=getattr(address, "vuna_preferred_delivery_to", None),
		average_service_duration=getattr(address, "vuna_average_service_duration", None),
		maximum_vehicle_category=getattr(address, "vuna_maximum_vehicle_category", None),
		requires_tail_lift=getattr(address, "vuna_requires_tail_lift", 0),
		loading_dock_available=getattr(address, "vuna_loading_dock_available", 0),
		restricted_access=getattr(address, "vuna_restricted_access", 0),
		access_notes=getattr(address, "vuna_access_notes", None),
	)


def get_warehouse_planning_data(warehouse):
	if not warehouse:
		return frappe._dict()
	address = get_default_address("Warehouse", warehouse)
	return get_address_planning_data(address)


def set_delivery_order_location_snapshots(delivery_order, overwrite=False):
	"""Copy current Address coordinates without changing snapshots on later saves."""
	destination = get_address_planning_data(delivery_order.delivery_address)
	source = get_warehouse_planning_data(delivery_order.source_warehouse)

	_set_if_empty(delivery_order, "transport_zone", destination.delivery_zone, overwrite)
	_set_if_empty(delivery_order, "destination_location", destination.location, overwrite)
	_set_if_empty(delivery_order, "destination_latitude", destination.latitude, overwrite)
	_set_if_empty(delivery_order, "destination_longitude", destination.longitude, overwrite)
	_set_if_empty(delivery_order, "delivery_from_time", destination.delivery_from_time, overwrite)
	_set_if_empty(delivery_order, "delivery_to_time", destination.delivery_to_time, overwrite)
	_set_if_empty(
		delivery_order, "average_service_duration", destination.average_service_duration, overwrite
	)
	_set_if_empty(
		delivery_order, "maximum_vehicle_category", destination.maximum_vehicle_category, overwrite
	)
	_set_if_empty(delivery_order, "tail_lift_required", destination.requires_tail_lift, overwrite)
	_set_if_empty(
		delivery_order, "loading_dock_available", destination.loading_dock_available, overwrite
	)
	_set_if_empty(delivery_order, "restricted_access", destination.restricted_access, overwrite)
	_set_if_empty(delivery_order, "access_notes", destination.access_notes, overwrite)
	_set_if_empty(delivery_order, "source_address", source.address, overwrite)
	_set_if_empty(delivery_order, "source_location", source.location, overwrite)
	_set_if_empty(delivery_order, "source_latitude", source.latitude, overwrite)
	_set_if_empty(delivery_order, "source_longitude", source.longitude, overwrite)


def get_point_coordinates(location):
	"""Return latitude and longitude from a Frappe Geolocation FeatureCollection."""
	if not location:
		return None, None
	try:
		geojson = frappe.parse_json(location) if isinstance(location, str) else location
		for feature in geojson.get("features", []):
			geometry = feature.get("geometry") or {}
			coordinates = geometry.get("coordinates") or []
			if geometry.get("type") == "Point" and len(coordinates) >= 2:
				return coordinates[1], coordinates[0]
	except (TypeError, ValueError, AttributeError):
		pass
	return None, None


def _set_if_empty(doc, fieldname, value, overwrite):
	if value is not None and (overwrite or not doc.get(fieldname)):
		doc.set(fieldname, value)
