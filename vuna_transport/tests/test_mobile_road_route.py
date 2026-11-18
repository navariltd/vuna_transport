"""Assigned-driver geometry is explicit, bounded and separate from execution/costs."""

import unittest
from unittest.mock import Mock, patch

import frappe

from vuna_transport.services.mobile import mobile_road_route_service as mobile
from vuna_transport.services.routing import google_routes_service as google
from vuna_transport.services.routing import road_routing_service as routing


class TestMobileRoadRoute(unittest.TestCase):
	def setUp(self):
		self.enterContext(patch.object(frappe, "throw", side_effect=ValueError))
		self.enterContext(patch.object(mobile, "_", side_effect=lambda value: value))
		self.enterContext(patch.object(google, "_", side_effect=lambda value: value))
		self.enterContext(patch.object(routing, "_", side_effect=lambda value: value))
		self.driver = self.enterContext(
			patch.object(mobile, "_require_mobile_driver", return_value=frappe._dict(name="DRIVER"))
		)
		self.trip = frappe._dict(
			name="TRIP",
			company="Company",
			driver_address="ORIGIN",
			docstatus=1,
			delivery_stops=[
				frappe._dict(name="STOP-B", idx=2, address="B"),
				frappe._dict(name="STOP-A", idx=1, address="A"),
			],
		)
		self.assigned = self.enterContext(patch.object(mobile, "_get_assigned_trip", return_value=self.trip))
		self.settings = frappe._dict(
			enabled=1, routing_provider="Google", mobile_map_provider="Automatic", allow_driver_road_routes=1
		)
		self.settings.get_password = Mock(return_value="server-secret")
		self.enterContext(patch.object(frappe, "get_cached_doc", return_value=self.settings))
		self.loads = self.enterContext(patch.object(mobile, "get_delivery_trip_loads", return_value=[]))
		self.address = self.enterContext(
			patch.object(mobile, "get_address_planning_data", return_value={"latitude": -1, "longitude": 37})
		)
		self.reserve = self.enterContext(patch.object(mobile, "reserve_usage"))
		self.compute = self.enterContext(
			patch.object(mobile, "compute_ordered_route", return_value={"encoded_polylines": ["??AA"]})
		)

	def test_saved_stop_order_and_colocated_visits_are_preserved(self):
		result = mobile.get_trip_road_route("INSTALL", "TRIP", "PHONE")
		self.driver.assert_called_once_with("INSTALL", "PHONE")
		self.assigned.assert_called_once_with("TRIP", "DRIVER")
		self.assertEqual(result["stop_order"], ["STOP-A", "STOP-B"])
		self.assertEqual([call.args[0] for call in self.address.call_args_list], ["ORIGIN", "A", "B"])
		self.assertEqual(len(self.compute.call_args.args[0]), 3)
		self.assertEqual(self.compute.call_args.kwargs, {"include_geometry": True})
		self.assertNotIn("server-secret", str(result))
		self.assertNotIn("distance_meters", result)
		self.assertEqual(self.trip.delivery_stops[0].name, "STOP-B")
		self.reserve.assert_called_once_with(self.settings, requests=1, mobile=True)

	def test_return_leg_uses_saved_trip_flag(self):
		self.trip.vuna_route_return_to_start = 1
		result = mobile.get_trip_road_route("INSTALL", "TRIP")
		points = self.compute.call_args.args[0]
		self.assertEqual(points[0], points[-1])
		self.assertTrue(result["return_to_start"])

	def test_unauthorized_device_or_trip_never_calls_provider(self):
		for guard in (self.driver, self.assigned):
			with self.subTest(guard=guard):
				guard.side_effect = ValueError("Denied")
				with self.assertRaises(ValueError):
					mobile.get_trip_road_route("INSTALL", "TRIP")
				guard.side_effect = None
		self.compute.assert_not_called()
		self.reserve.assert_not_called()

	def test_assigned_driver_needs_no_planning_role(self):
		with patch.object(
			frappe, "get_roles", side_effect=AssertionError("Planning roles must not be checked")
		):
			result = mobile.get_trip_road_route("INSTALL", "TRIP")
		self.assertEqual(result["trip_name"], "TRIP")

	def test_disabled_or_unconfigured_driver_permission_never_calls_google(self):
		for value in (None, 0, "0"):
			self.settings.allow_driver_road_routes = value
			with self.assertRaises(ValueError):
				mobile.get_trip_road_route("INSTALL", "TRIP")
		self.compute.assert_not_called()
		self.reserve.assert_not_called()

	def test_disabled_or_other_map_provider_never_calls_google(self):
		for field, value in (
			("enabled", 0),
			("routing_provider", "Geographic Estimate"),
			("mobile_map_provider", "OpenStreetMap"),
		):
			with self.subTest(field=field):
				old = self.settings[field]
				self.settings[field] = value
				with self.assertRaises(ValueError):
					mobile.get_trip_road_route("INSTALL", "TRIP")
				self.settings[field] = old
		self.compute.assert_not_called()

	def test_limits_and_missing_coordinates_fail_before_usage_reservation(self):
		self.settings.routing_max_locations = 1
		with self.assertRaises(ValueError):
			mobile.get_trip_road_route("INSTALL", "TRIP")
		self.settings.routing_max_locations = 100
		for data in ({}, {"latitude": 0, "longitude": 0}, {"latitude": 91, "longitude": 37}):
			self.address.return_value = data
			with self.assertRaises(ValueError):
				mobile.get_trip_road_route("INSTALL", "TRIP")
		self.compute.assert_not_called()
		self.reserve.assert_not_called()

	def test_large_trip_reserves_every_batch_without_reordering(self):
		self.trip.delivery_stops = [
			frappe._dict(name=f"STOP-{i}", idx=i, address=f"A-{i}") for i in range(1, 101)
		]
		result = mobile.get_trip_road_route("INSTALL", "TRIP")
		self.reserve.assert_called_once_with(self.settings, requests=4, mobile=True)
		self.assertEqual(len(result["stop_order"]), 100)

	def test_loading_warehouse_resolves_origin_across_multiple_loads(self):
		self.loads.return_value = [frappe._dict(company="Company", source_warehouse="MAIN") for _ in range(2)]
		with (
			patch.object(
				frappe, "get_doc", return_value=frappe._dict(company="Company", disabled=0, is_group=0)
			),
			patch.object(mobile, "get_warehouse_planning_data", return_value={"address": "MAIN-ADDRESS"}),
		):
			mobile.get_trip_road_route("INSTALL", "TRIP")
		self.assertEqual(self.address.call_args_list[0].args[0], "MAIN-ADDRESS")
		self.loads.return_value[1].source_warehouse = "OTHER"
		with self.assertRaises(ValueError):
			mobile.get_trip_road_route("INSTALL", "TRIP")

	def test_quota_failure_has_no_google_request(self):
		self.reserve.side_effect = ValueError("Quota")
		with self.assertRaises(ValueError):
			mobile.get_trip_road_route("INSTALL", "TRIP")
		self.compute.assert_not_called()


class TestGoogleDisplayGeometry(unittest.TestCase):
	def setUp(self):
		self.enterContext(patch.object(frappe, "throw", side_effect=ValueError))
		self.enterContext(patch.object(google, "_", side_effect=lambda value: value))
		self.points = [{"latitude": -1, "longitude": 37}] * 2
		self.route = {
			"legs": [{"distanceMeters": 1, "duration": "1s"}],
			"polyline": {"encodedPolyline": "??AA"},
		}

	def test_geometry_is_requested_only_when_explicit(self):
		response = Mock(status_code=200)
		response.json.return_value = {"routes": [self.route]}
		with patch.object(google.requests, "post", return_value=response) as post:
			result = google.compute_route(self.points, "secret", include_geometry=True)
		self.assertEqual(result["encoded_polyline"], "??AA")
		self.assertIn("routes.polyline.encodedPolyline", post.call_args.kwargs["headers"]["X-Goog-FieldMask"])
		self.assertEqual(post.call_args.kwargs["json"]["polylineQuality"], "OVERVIEW")
		self.assertNotIn("optimizeWaypointOrder", post.call_args.kwargs["json"])

	def test_missing_or_invalid_geometry_is_rejected_safely(self):
		for geometry in (None, [], {}, {"encodedPolyline": "x"}, {"encodedPolyline": "\nsecret"}):
			response = Mock(status_code=200)
			response.json.return_value = {"routes": [{**self.route, "polyline": geometry}]}
			with patch.object(google.requests, "post", return_value=response), self.assertRaises(ValueError):
				google.compute_route(self.points, "secret", include_geometry=True)

	def test_batch_polylines_remain_separate_and_share_one_boundary(self):
		points = [{"latitude": 0, "longitude": i} for i in range(40)]
		with patch.object(
			google,
			"compute_route",
			return_value={
				"distance_meters": 1,
				"duration_seconds": 1,
				"legs": [],
				"encoded_polyline": "??AA",
			},
		) as compute:
			result = google.compute_ordered_route(points, "secret", include_geometry=True)
		self.assertEqual(result["encoded_polylines"], ["??AA", "??AA"])
		self.assertEqual(compute.call_args_list[0].args[0][-1], compute.call_args_list[1].args[0][0])
