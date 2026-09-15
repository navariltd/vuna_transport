"""Resolve, normalize, and snapshot operational locations."""

import json

import frappe
from frappe import _
from frappe.contacts.doctype.address.address import get_default_address


def get_address_planning_data(address_name):
	if not address_name or not frappe.db.exists("Address", address_name):
		return frappe._dict()

	address = frappe.get_cached_doc("Address", address_name)
	location = getattr(address, "vuna_location", None)
	map_latitude, map_longitude = get_point_coordinates(location)
	latitude = _coordinate_or_fallback(getattr(address, "vuna_latitude", None), map_latitude)
	longitude = _coordinate_or_fallback(getattr(address, "vuna_longitude", None), map_longitude)
	return frappe._dict(
		address=address.name,
		province=getattr(address, "vuna_province", None),
		county=getattr(address, "vuna_county", None),
		town=getattr(address, "vuna_town", None),
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


def synchronize_address_location(address, method=None):
	"""Synchronize Vuna geography and coordinates with the standard Address fields."""
	_sync_address_geography(address)

	if not all(
		address.meta.has_field(fieldname)
		for fieldname in ("vuna_latitude", "vuna_longitude", "vuna_location")
	):
		return

	coordinates_changed = address.is_new() or any(
		address.has_value_changed(fieldname) for fieldname in ("vuna_latitude", "vuna_longitude")
	)
	location_changed = address.is_new() or address.has_value_changed("vuna_location")
	latitude = address.get("vuna_latitude")
	longitude = address.get("vuna_longitude")
	coordinates_present = _has_coordinate(latitude) or _has_coordinate(longitude)

	# Explicit coordinate entry takes precedence when both representations change.
	if coordinates_changed and coordinates_present:
		latitude, longitude = validate_point_coordinates(latitude, longitude)
		address.vuna_latitude = latitude
		address.vuna_longitude = longitude
		address.vuna_location = make_point_geojson(latitude, longitude)
		return

	if location_changed:
		if not address.vuna_location:
			address.vuna_latitude = None
			address.vuna_longitude = None
			return
		latitude, longitude = get_point_coordinates(address.vuna_location)
		if latitude is None or longitude is None:
			frappe.throw(_("Delivery Location must contain a point marker rather than a line or area."))
		address.vuna_latitude = latitude
		address.vuna_longitude = longitude
		return

	# Normalize records created before explicit coordinate fields were introduced.
	if address.vuna_location and not coordinates_present:
		latitude, longitude = get_point_coordinates(address.vuna_location)
		if latitude is not None and longitude is not None:
			address.vuna_latitude = latitude
			address.vuna_longitude = longitude
	elif coordinates_present and not address.vuna_location:
		latitude, longitude = validate_point_coordinates(latitude, longitude)
		address.vuna_location = make_point_geojson(latitude, longitude)
	else:
		validate_point_coordinates(latitude, longitude, allow_empty=True)


def _sync_address_geography(address):
	"""Keep ERPNext's display/search fields populated from structured Vuna geography."""
	geography_fields = ("vuna_province", "vuna_county", "vuna_town")
	if not all(address.meta.has_field(fieldname) for fieldname in geography_fields):
		return

	_validate_address_geography(address)

	if address.get("vuna_province"):
		address.state = address.vuna_province
	if address.get("vuna_county"):
		address.county = address.vuna_county
	if address.get("vuna_town"):
		address.city = address.vuna_town


def _validate_address_geography(address):
	"""Ensure the selected location links follow Country → Province → County → Town."""
	province = address.get("vuna_province")
	county = address.get("vuna_county")
	town = address.get("vuna_town")

	if province and address.get("country"):
		province_country = frappe.db.get_value("Province", province, "country")
		if province_country and province_country != address.country:
			frappe.throw(_("Province {0} does not belong to Country {1}.").format(province, address.country))

	if county and province:
		county_province = frappe.db.get_value("County", county, "province")
		if county_province and county_province != province:
			frappe.throw(_("County {0} does not belong to Province {1}.").format(county, province))

	if town and county:
		town_county = frappe.db.get_value("Town", town, "county")
		if town_county and town_county != county:
			frappe.throw(_("Town {0} does not belong to County {1}.").format(town, county))


def backfill_address_coordinates():
	"""Populate explicit coordinates after app custom fields have been synchronized."""
	if not all(
		frappe.db.has_column("Address", fieldname)
		for fieldname in ("vuna_location", "vuna_latitude", "vuna_longitude")
	):
		return
	for address in frappe.get_all(
		"Address",
		filters={"vuna_location": ["is", "set"]},
		fields=["name", "vuna_location", "vuna_latitude", "vuna_longitude"],
	):
		if _has_coordinate(address.vuna_latitude) and _has_coordinate(address.vuna_longitude):
			continue
		latitude, longitude = get_point_coordinates(address.vuna_location)
		if latitude is None or longitude is None:
			continue
		frappe.db.set_value(
			"Address",
			address.name,
			{
				"vuna_latitude": latitude,
				"vuna_longitude": longitude,
			},
			update_modified=False,
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
	_set_if_empty(delivery_order, "average_service_duration", destination.average_service_duration, overwrite)
	_set_if_empty(delivery_order, "maximum_vehicle_category", destination.maximum_vehicle_category, overwrite)
	_set_if_empty(delivery_order, "tail_lift_required", destination.requires_tail_lift, overwrite)
	_set_if_empty(delivery_order, "loading_dock_available", destination.loading_dock_available, overwrite)
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


def make_point_geojson(latitude, longitude):
	"""Build Frappe Geolocation GeoJSON from latitude and longitude."""
	latitude, longitude = validate_point_coordinates(latitude, longitude)
	return json.dumps(
		{
			"type": "FeatureCollection",
			"features": [
				{
					"type": "Feature",
					"properties": {},
					"geometry": {
						"type": "Point",
						"coordinates": [longitude, latitude],
					},
				}
			],
		},
		separators=(",", ":"),
	)


def validate_point_coordinates(latitude, longitude, allow_empty=False):
	latitude_present = _has_coordinate(latitude)
	longitude_present = _has_coordinate(longitude)
	if not latitude_present and not longitude_present and allow_empty:
		return None, None
	if not latitude_present or not longitude_present:
		frappe.throw(_("Enter both Latitude and Longitude."))
	try:
		latitude = float(latitude)
		longitude = float(longitude)
	except (TypeError, ValueError):
		frappe.throw(_("Latitude and Longitude must be numeric values."))
	if not -90 <= latitude <= 90:
		frappe.throw(_("Latitude must be between -90 and 90."))
	if not -180 <= longitude <= 180:
		frappe.throw(_("Longitude must be between -180 and 180."))
	return latitude, longitude


def _has_coordinate(value):
	return value is not None and value != ""


def _coordinate_or_fallback(value, fallback):
	return value if _has_coordinate(value) else fallback


def _set_if_empty(doc, fieldname, value, overwrite):
	if value is not None and (overwrite or not doc.get(fieldname)):
		doc.set(fieldname, value)
