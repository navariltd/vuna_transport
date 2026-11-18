"""Bounded Google Routes requests. No route geometry, caching or document writes."""

import math
import re

import frappe
import requests
from frappe import _

ENDPOINT = "https://routes.googleapis.com/directions/v2:computeRoutes"
FIELD_MASK = "routes.distanceMeters,routes.duration,routes.legs.distanceMeters,routes.legs.duration"
MAX_POINTS = 27  # Origin, destination and at most 25 intermediates.


def compute_route(points, api_key):
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
	failed = False
	try:
		response = requests.post(
			ENDPOINT,
			json=body,
			headers={
				"X-Goog-Api-Key": api_key,
				"X-Goog-FieldMask": FIELD_MASK,
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
	return parse_route(data, len(points) - 1)


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
