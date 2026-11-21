"""Bounded Google Routes requests; optional display geometry, never document writes."""

import math
import re

import frappe
import requests
from frappe import _

ENDPOINT = "https://routes.googleapis.com/directions/v2:computeRoutes"
FIELD_MASK = "routes.distanceMeters,routes.duration,routes.legs.distanceMeters,routes.legs.duration"
MAX_POINTS = 27  # Origin, destination and at most 25 intermediates.


def split_route_points(points):
	return [points[index : index + MAX_POINTS] for index in range(0, len(points) - 1, MAX_POINTS - 1)]


def compute_ordered_route(points, api_key, *, include_geometry=False):
	"""One continuous journey: a shared boundary, never a repeated road leg."""
	results = [
		compute_route(batch, api_key, include_geometry=True)
		if include_geometry
		else compute_route(batch, api_key)
		for batch in split_route_points(points)
	]
	result = {
		"distance_meters": sum(result["distance_meters"] for result in results),
		"duration_seconds": sum(result["duration_seconds"] for result in results),
		"legs": [leg for result in results for leg in result["legs"]],
	}
	if include_geometry:
		# Each batch is encoded independently; never concatenate encoded strings.
		result["encoded_polylines"] = [row["encoded_polyline"] for row in results]
	return result


def compute_route(points, api_key, *, include_geometry=False):
	if not 2 <= len(points) <= MAX_POINTS:
		frappe.throw(_("Google road routing supports 2 to 27 route points per preview."))
	waypoints = [
		{"location": {"latLng": {"latitude": point["latitude"], "longitude": point["longitude"]}}}
		for point in points
	]
	body = {
		"origin": waypoints[0],
		"destination": waypoints[-1],
		"intermediates": waypoints[1:-1],
		"travelMode": "DRIVE",
		"routingPreference": "TRAFFIC_UNAWARE",
		"computeAlternativeRoutes": False,
		"units": "METRIC",
		"languageCode": "en",
	}
	if include_geometry:
		body.update(polylineEncoding="ENCODED_POLYLINE", polylineQuality="OVERVIEW")
	failed = False
	try:
		response = requests.post(
			ENDPOINT,
			json=body,
			headers={
				"X-Goog-Api-Key": api_key,
				"X-Goog-FieldMask": FIELD_MASK
				+ (",routes.polyline.encodedPolyline" if include_geometry else ""),
				"Content-Type": "application/json",
			},
			timeout=(5, 15),
			allow_redirects=False,
		)
	except requests.RequestException:
		failed = True
	if failed:
		frappe.throw(
			_("Google road routing is unavailable. Please try again; no transport records were changed.")
		)
	if response.status_code != 200:
		_raise_provider_error(response)
	data = None
	try:
		data = response.json()
	except ValueError:
		pass
	result = parse_route(data, len(points) - 1)
	if include_geometry:
		geometry = data["routes"][0].get("polyline")
		polyline = geometry.get("encodedPolyline") if isinstance(geometry, dict) else None
		if not isinstance(polyline, str) or not re.fullmatch(r"[\x3f-\x7e]{2,200000}", polyline):
			frappe.throw(_("Google returned no usable road route line. No transport records were changed."))
		result["encoded_polyline"] = polyline
	return result


def _raise_provider_error(response):
	# Never echo Google's free-text message, request headers or raw response:
	# these can contain coordinates, credentials or project identifiers.
	reasons = set()
	try:
		error = response.json().get("error", {})
		for detail in error.get("details", []):
			if isinstance(detail, dict) and isinstance(detail.get("reason"), str):
				reasons.add(detail["reason"])
	except (ValueError, AttributeError, TypeError):
		pass
	if reasons & {"API_KEY_INVALID", "API_KEY_EXPIRED", "API_KEY_NOT_FOUND"}:
		frappe.throw(
			_("Google rejected the API key. Check the key in Transport Settings or Google Settings.")
		)
	if "SERVICE_DISABLED" in reasons:
		frappe.throw(_("Enable Routes API in the Google Cloud project that owns this API key."))
	if "BILLING_DISABLED" in reasons:
		frappe.throw(_("Enable billing for the Google Cloud project that owns this API key."))
	if reasons & {"API_KEY_SERVICE_BLOCKED", "API_KEY_IP_ADDRESS_BLOCKED", "API_KEY_HTTP_REFERRER_BLOCKED"}:
		frappe.throw(_("Google rejected the API key restrictions. Allow server requests to Routes API."))
	if response.status_code == 429:
		frappe.throw(_("Google routing quota was exceeded. Check your Google Cloud quota and billing."))
	if response.status_code in (401, 403):
		frappe.throw(
			_(
				"Google routing access was denied. Check the API key, Routes API enablement and key restrictions."
			)
		)
	if response.status_code == 400:
		frappe.throw(
			_(
				"Google rejected the route request (HTTP 400). Check the API key and exact endpoint coordinates."
			)
		)
	frappe.throw(
		_(
			"Google could not calculate this road route (HTTP {0}). Check the locations or try again later."
		).format(int(response.status_code))
	)


def parse_route(data, expected_legs):
	try:
		route = data["routes"][0]
		legs = route["legs"]
		if not isinstance(legs, list) or len(legs) != expected_legs:
			raise ValueError
		return {
			"distance_meters": _distance(route.get("distanceMeters", 0)),
			"duration_seconds": _duration(route.get("duration", "0s")),
			"legs": [
				{
					"distance_meters": _distance(leg.get("distanceMeters", 0)),
					"duration_seconds": _duration(leg.get("duration", "0s")),
				}
				for leg in legs
			],
		}
	except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError):
		pass
	frappe.throw(_("Google returned no complete road route for these stops. No estimates were changed."))


def _distance(value):
	if (
		isinstance(value, bool)
		or not isinstance(value, (int, float))
		or not math.isfinite(value)
		or value < 0
	):
		raise ValueError
	return float(value)


def _duration(value):
	if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+)?s", value):
		raise ValueError
	return _distance(float(value[:-1]))
