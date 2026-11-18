"""Single-vehicle Google OptimizeTours adapter; no content persistence or geometry."""

import json
import re
from datetime import UTC, datetime, timedelta

import frappe
import requests
from frappe import _
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from vuna_transport.services.routing.google_routes_service import _distance, _duration, _raise_provider_error
from vuna_transport.services.routing.routing_policy_service import reserve_usage

SCOPE = "https://www.googleapis.com/auth/cloud-platform"
TOKEN_URI = "https://oauth2.googleapis.com/token"


def parse_credentials(secret):
	info = None
	try:
		info = json.loads(secret)
		if (
			not isinstance(info, dict)
			or info.get("type") != "service_account"
			or info.get("token_uri") != TOKEN_URI
			or not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", info.get("project_id", ""))
		):
			raise ValueError
		credentials = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
		return info["project_id"], credentials
	except (ValueError, TypeError, KeyError, GoogleAuthError):
		pass
	frappe.throw(
		_(
			"Set valid Google service-account JSON credentials in Transport Settings. No Google request was sent."
		)
	)


def optimize_route(origin, stops, settings, *, return_to_start=False):
	secret = settings.get_password("google_optimization_service_account", raise_exception=False)
	project, credentials = parse_credentials(secret)
	body = build_request(origin, stops, return_to_start=return_to_start)
	reserve_usage(settings, requests=1, shipments=len(stops))
	failed = False
	try:
		transport = Request()
		credentials.refresh(lambda **kwargs: transport(**{**kwargs, "timeout": 15}))
	except (GoogleAuthError, requests.RequestException, ValueError):
		failed = True
	if failed:
		frappe.throw(
			_(
				"Google service-account authentication failed. Check its credentials and Google Cloud configuration."
			)
		)
	response = None
	try:
		response = requests.post(
			f"https://routeoptimization.googleapis.com/v1/projects/{project}:optimizeTours",
			json=body,
			headers={"Authorization": "Bearer " + credentials.token, "Content-Type": "application/json"},
			timeout=(5, 25),
			allow_redirects=False,
		)
	except requests.RequestException:
		pass
	if response is None:
		frappe.throw(_("Google Route Optimization is unavailable. No stop order was changed."))
	if response.status_code in (401, 403):
		frappe.throw(
			_(
				"Google Route Optimization access was denied. Enable the API and check the service account's routeoptimization.locations.use permission and billing."
			)
		)
	if response.status_code == 400:
		frappe.throw(
			_(
				"Google rejected the optimization request. Check the exact delivery locations and Google Cloud configuration."
			)
		)
	if response.status_code != 200:
		_raise_provider_error(response)
	data = None
	try:
		data = response.json()
	except ValueError:
		pass
	return parse_result(data, len(stops), return_to_start=return_to_start)


def build_request(origin, stops, *, return_to_start):
	def location(point):
		return {"latitude": point["latitude"], "longitude": point["longitude"]}

	vehicle = {"startLocation": location(origin), "travelMode": "DRIVING", "costPerTraveledHour": 1}
	if return_to_start:
		vehicle["endLocation"] = location(origin)
	start = datetime.now(UTC)
	return {
		"timeout": "10s",
		"considerRoadTraffic": False,
		"populatePolylines": False,
		"populateTransitionPolylines": False,
		"useGeodesicDistances": False,
		"model": {
			"globalStartTime": start.isoformat().replace("+00:00", "Z"),
			"globalEndTime": (start + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
			"vehicles": [vehicle],
			# Omit penaltyCost: every delivery location is mandatory. Never send
			# customer names, item data or ERP document identifiers to Google.
			"shipments": [{"deliveries": [{"arrivalLocation": location(point)}]} for point in stops],
		},
	}


def parse_result(data, stop_count, *, return_to_start):
	try:
		if data.get("skippedShipments") or data.get("validationErrors") or len(data["routes"]) != 1:
			raise ValueError
		route = data["routes"][0]
		if type(route.get("vehicleIndex", 0)) is not int or route.get("vehicleIndex", 0) != 0:
			raise ValueError
		visits = route["visits"]
		order = [visit.get("shipmentIndex", 0) for visit in visits]
		if (
			any(type(index) is not int for index in order)
			or sorted(order) != list(range(stop_count))
			or any(
				visit.get("isPickup", False)
				or type(visit.get("visitRequestIndex", 0)) is not int
				or visit.get("visitRequestIndex", 0) != 0
				for visit in visits
			)
		):
			raise ValueError
		transitions = route["transitions"]
		if len(transitions) != stop_count + 1:
			raise ValueError
		legs = [
			{
				"distance_meters": _distance(transition.get("travelDistanceMeters", 0)),
				"duration_seconds": _duration(transition.get("travelDuration", "0s")),
			}
			for transition in transitions
		]
		if not return_to_start:
			if legs[-1]["distance_meters"] or legs[-1]["duration_seconds"]:
				raise ValueError
			legs.pop()  # Google's zero-distance transition to an unspecified end.
		return {
			"stop_order": order,
			"distance_meters": sum(leg["distance_meters"] for leg in legs),
			"duration_seconds": sum(leg["duration_seconds"] for leg in legs),
			"legs": legs,
		}
	except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError):
		pass
	frappe.throw(
		_(
			"Google did not return a complete delivery order. No stops were applied; check for unreachable locations."
		)
	)
