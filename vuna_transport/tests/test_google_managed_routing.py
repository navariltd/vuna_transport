"""Managed optimization, large-route batching and routing policy boundaries."""

import copy
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import frappe
import requests

from vuna_transport.services.routing import google_optimization_service as managed
from vuna_transport.services.routing import google_routes_service as routes
from vuna_transport.services.routing import routing_policy_service as policy


def optimizer_response(count=40, closed=False):
	return {
		"routes": [
			{
				"visits": [{"shipmentIndex": index} for index in reversed(range(count))],
				"transitions": [{"travelDistanceMeters": 1000, "travelDuration": "60s"} for _ in range(count)]
				+ [
					{
						"travelDistanceMeters": 500 if closed else 0,
						"travelDuration": "30s" if closed else "0s",
					}
				],
			}
		]
	}


class TestManagedOptimization(unittest.TestCase):
	def setUp(self):
		self.enterContext(patch.object(managed, "_", side_effect=lambda text: text))
		self.enterContext(patch.object(managed.frappe, "throw", side_effect=ValueError))
		self.origin = {"latitude": 1, "longitude": 37, "label": "Private origin"}
		self.stops = [
			{"latitude": 1 + index / 100, "longitude": 37, "label": "Private customer"} for index in range(40)
		]

	def test_open_request_uses_one_vehicle_and_all_mandatory_locations(self):
		body = managed.build_request(self.origin, self.stops, return_to_start=False)
		self.assertEqual(len(body["model"]["vehicles"]), 1)
		self.assertEqual(len(body["model"]["shipments"]), 40)
		self.assertNotIn("endLocation", body["model"]["vehicles"][0])
		self.assertTrue(all("penaltyCost" not in shipment for shipment in body["model"]["shipments"]))
		self.assertFalse(body["useGeodesicDistances"])
		self.assertFalse(body["populatePolylines"])
		self.assertNotIn("Private", json.dumps(body))
		closed = managed.build_request(self.origin, self.stops, return_to_start=True)
		self.assertEqual(closed["model"]["vehicles"][0]["endLocation"], {"latitude": 1, "longitude": 37})

	def test_complete_open_and_return_routes_validate_all_locations(self):
		for count in (2, 5, 40):
			for closed in (False, True):
				with self.subTest(count=count, closed=closed):
					result = managed.parse_result(
						optimizer_response(count, closed=closed), count, return_to_start=closed
					)
					self.assertEqual(result["stop_order"], list(reversed(range(count))))
					self.assertEqual(len(result["legs"]), count + int(closed))
					self.assertEqual(result["distance_meters"], count * 1000 + (500 if closed else 0))

	def test_skipped_duplicate_missing_invalid_or_partial_results_are_rejected(self):
		good = optimizer_response(2)
		bad_results = [None, {}, {**good, "skippedShipments": [{"index": 0}]}, {"routes": []}]
		for mutation in (
			lambda row: row["routes"][0]["visits"].pop(),
			lambda row: row["routes"][0]["visits"][0].update(shipmentIndex=0),
			lambda row: row["routes"][0]["visits"][0].update(shipmentIndex=True),
			lambda row: row["routes"][0]["visits"][0].update(isPickup=True),
			lambda row: row["routes"][0]["transitions"].pop(),
			lambda row: row["routes"][0]["transitions"][0].update(travelDistanceMeters=-1),
			lambda row: row["routes"][0]["transitions"][-1].update(travelDistanceMeters=1),
		):
			data = copy.deepcopy(good)
			mutation(data)
			bad_results.append(data)
		for data in bad_results:
			with self.subTest(data=data), self.assertRaises(ValueError):
				managed.parse_result(data, 2, return_to_start=False)

	def test_credentials_cannot_redirect_token_exchange_or_inject_project_path(self):
		info = {"type": "service_account", "project_id": "sample-project", "token_uri": managed.TOKEN_URI}
		with patch.object(managed.service_account.Credentials, "from_service_account_info") as create:
			for changes in (
				{"token_uri": "https://example.com/token"},
				{"project_id": "../other"},
				{"type": "authorized_user"},
			):
				with self.subTest(changes=changes), self.assertRaises(ValueError):
					managed.parse_credentials(json.dumps({**info, **changes}))
			create.assert_not_called()
			project, _credentials = managed.parse_credentials(json.dumps(info))
			self.assertEqual(project, "sample-project")
			self.assertEqual(create.call_args.kwargs["scopes"], [managed.SCOPE])

	def test_oauth_request_is_fixed_bounded_and_reserves_location_usage(self):
		settings = Mock()
		settings.get_password.return_value = "secret-json"
		credentials = Mock(token="secret-token")
		response = Mock(status_code=200)
		response.json.return_value = optimizer_response()
		with (
			patch.object(managed, "parse_credentials", return_value=("sample-project", credentials)),
			patch.object(managed, "reserve_usage") as reserve,
			patch.object(managed.requests, "post", return_value=response) as post,
		):
			result = managed.optimize_route(self.origin, self.stops, settings)
		reserve.assert_called_once_with(settings, requests=1, shipments=40)
		credentials.refresh.assert_called_once()
		self.assertEqual(
			post.call_args.args[0],
			"https://routeoptimization.googleapis.com/v1/projects/sample-project:optimizeTours",
		)
		self.assertEqual(post.call_args.kwargs["timeout"], (5, 25))
		self.assertFalse(post.call_args.kwargs["allow_redirects"])
		self.assertNotIn("secret", json.dumps(post.call_args.kwargs["json"]))
		self.assertNotIn("secret", json.dumps(result))

	def test_exhausted_quota_blocks_auth_and_external_request(self):
		credentials = Mock(token="secret")
		with (
			patch.object(managed, "parse_credentials", return_value=("sample-project", credentials)),
			patch.object(managed, "reserve_usage", side_effect=ValueError),
			patch.object(managed.requests, "post") as post,
			self.assertRaises(ValueError),
		):
			managed.optimize_route(self.origin, self.stops, Mock())
		credentials.refresh.assert_not_called()
		post.assert_not_called()

	def test_provider_denial_and_network_errors_never_echo_credentials(self):
		for status in (400, 401, 403, 429, 500):
			response = Mock(status_code=status)
			response.json.return_value = {"error": {"message": "secret"}}
			with (
				self.subTest(status=status),
				patch.object(
					managed, "parse_credentials", return_value=("sample-project", Mock(token="secret"))
				),
				patch.object(managed, "reserve_usage"),
				patch.object(managed.requests, "post", return_value=response),
				patch.object(routes, "_", side_effect=lambda text: text),
			):
				with self.assertRaises(ValueError):
					managed.optimize_route(self.origin, self.stops, Mock())
				self.assertNotIn("secret", managed.frappe.throw.call_args.args[0])


class TestLargeRouteBatching(unittest.TestCase):
	def test_boundaries_overlap_but_legs_do_not(self):
		for count in (2, 27, 28, 41, 42, 102, 202):
			points = list(range(count))
			batches = routes.split_route_points(points)
			self.assertTrue(all(2 <= len(batch) <= 27 for batch in batches))
			self.assertEqual(sum(len(batch) - 1 for batch in batches), count - 1)
			for left, right in pairwise(batches):
				self.assertEqual(left[-1], right[0])

	def test_forty_locations_sum_all_legs_once(self):
		def compute(points, key):
			legs = [
				{"from": left, "to": right, "distance_meters": 1000, "duration_seconds": 60}
				for left, right in pairwise(points)
			]
			return {"distance_meters": len(legs) * 1000, "duration_seconds": len(legs) * 60, "legs": legs}

		with patch.object(routes, "compute_route", side_effect=compute) as request:
			result = routes.compute_ordered_route(list(range(41)), "secret")
		self.assertEqual(request.call_count, 2)
		self.assertEqual(result["distance_meters"], 40000)
		self.assertEqual(result["duration_seconds"], 2400)
		self.assertEqual([(leg["from"], leg["to"]) for leg in result["legs"]], list(pairwise(range(41))))

	def test_failed_later_batch_does_not_return_partial_distance(self):
		with (
			patch.object(
				routes,
				"compute_route",
				side_effect=[{"distance_meters": 1, "duration_seconds": 1, "legs": []}, ValueError],
			),
			self.assertRaises(ValueError),
		):
			routes.compute_ordered_route(list(range(41)), "secret")


class TestRoutingPolicy(unittest.TestCase):
	def setUp(self):
		self.enterContext(patch.object(policy, "_", side_effect=lambda text: text))
		self.enterContext(patch.object(policy.frappe, "throw", side_effect=ValueError))

	def test_roles_are_admin_configurable_without_bypassing_document_permissions(self):
		settings = frappe._dict(routing_allowed_roles=[frappe._dict(role="Special Planner")])
		with patch.object(policy.frappe, "get_roles", return_value=["Transport Dispatcher"]):
			self.assertFalse(policy.allowed_role(settings))
		with patch.object(policy.frappe, "get_roles", return_value=["Special Planner"]):
			self.assertTrue(policy.allowed_role(settings))

	def test_positive_bounded_limits_and_defaults(self):
		self.assertEqual(policy.get_limit(frappe._dict(), "routing_max_locations"), 100)
		for value in (0, -1, 201, 1.5, "bad", True):
			with self.subTest(value=value), self.assertRaises(ValueError):
				policy.get_limit(frappe._dict(routing_max_locations=value), "routing_max_locations")

	def test_atomic_reservation_enforces_both_counters_without_refunding_failures(self):
		settings = frappe._dict(routing_daily_request_limit=2, routing_daily_shipment_limit=4)
		# Isolated Redis key: no real routing quota or Google request is touched.
		day = "test-" + uuid4().hex
		with patch.object(policy, "nowdate", return_value=day):
			key = frappe.cache.make_key("vuna_transport:routing:usage:" + day)
			try:
				policy.reserve_usage(settings, requests=1, shipments=2)
				policy.reserve_usage(settings, requests=1, shipments=2)
				with self.assertRaises(ValueError):
					policy.reserve_usage(settings, requests=1, shipments=1)
				self.assertEqual(int(frappe.cache.execute_command("HGET", key, "requests")), 2)
				self.assertEqual(int(frappe.cache.execute_command("HGET", key, "shipments")), 4)
			finally:
				frappe.cache.delete(key)

	def test_concurrent_reservations_cannot_exceed_either_daily_limit(self):
		cache = frappe.cache
		key = cache.make_key("vuna_transport:routing:concurrency-test:" + uuid4().hex)
		try:

			def reserve(_index):
				return cache.eval(policy.RESERVE_SCRIPT, 1, key, 1, 2, 20, 10)

			with ThreadPoolExecutor(max_workers=8) as pool:
				results = list(pool.map(reserve, range(20)))
			self.assertEqual(results.count(1), 5)
			self.assertEqual(results.count(-2), 15)
			self.assertEqual(int(cache.execute_command("HGET", key, "requests")), 5)
			self.assertEqual(int(cache.execute_command("HGET", key, "shipments")), 10)
		finally:
			cache.delete(key)
