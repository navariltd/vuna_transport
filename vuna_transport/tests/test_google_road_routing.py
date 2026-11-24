"""Google previews stay permission checked, ordered and independent of stock posting."""

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
import requests

from vuna_transport.services.routing import google_routes_service as google
from vuna_transport.services.routing import road_routing_service as routing


class TestGoogleRoutes(unittest.TestCase):
	def setUp(self):
		self.enterContext(patch.object(google, "_", side_effect=lambda value: value))
		self.enterContext(patch.object(google.frappe, "throw", side_effect=ValueError))
		self.points = [{"latitude": 0, "longitude": index, "label": "Private name"} for index in range(3)]
		self.route = {
			"distanceMeters": 1200,
			"duration": "123.5s",
			"legs": [
				{"distanceMeters": 500, "duration": "60s"},
				{"distanceMeters": 700, "duration": "63.5s"},
			],
		}

	def test_bounded_request_uses_coordinates_and_header_key_only(self):
		response = Mock(status_code=200)
		response.json.return_value = {"routes": [self.route]}
		with patch.object(google.requests, "post", return_value=response) as post:
			result = google.compute_route(self.points, "secret-key")
		args, kwargs = post.call_args
		self.assertEqual(args, (google.ENDPOINT,))
		self.assertEqual(kwargs["headers"]["X-Goog-Api-Key"], "secret-key")
		self.assertFalse(kwargs["allow_redirects"])
		self.assertEqual(kwargs["timeout"], (5, 15))
		self.assertNotIn("secret-key", str(kwargs["json"]))
		self.assertNotIn("Private name", str(kwargs["json"]))
		self.assertNotIn("polyline", kwargs["headers"]["X-Goog-FieldMask"])
		self.assertNotIn("optimizeWaypointOrder", kwargs["json"])
		self.assertEqual(kwargs["json"]["intermediates"][0]["location"]["latLng"]["longitude"], 1)
		self.assertEqual(result["distance_meters"], 1200)
		self.assertEqual(result["duration_seconds"], 123.5)

	def test_invalid_point_counts_never_request_google(self):
		with patch.object(google.requests, "post") as post:
			for count in (0, 1, 28):
				with self.subTest(count=count), self.assertRaises(ValueError):
					google.compute_route(self.points[:1] * count, "secret")
			post.assert_not_called()

	def test_provider_errors_do_not_echo_response_or_exception(self):
		for status in (301, 401, 403, 429, 500):
			with (
				self.subTest(status=status),
				patch.object(google.requests, "post", return_value=Mock(status_code=status, text="secret")),
			):
				with self.assertRaises(ValueError) as caught:
					google.compute_route(self.points, "secret")
				self.assertNotIn("secret", str(caught.exception))
		with patch.object(google.requests, "post", side_effect=requests.Timeout("secret")):
			with self.assertRaises(ValueError) as caught:
				google.compute_route(self.points, "secret")
			self.assertNotIn("secret", str(caught.exception))

	def test_incomplete_and_invalid_routes_rejected(self):
		for data in (
			None,
			{},
			{"routes": []},
			{"routes": [{"legs": []}]},
			{"routes": [dict(self.route, duration="NaNs")]},
			{"routes": [dict(self.route, distanceMeters=-1)]},
			{"routes": [dict(self.route, distanceMeters=True)]},
			{"routes": [dict(self.route, distanceMeters=float("inf"))]},
		):
			with self.subTest(data=data), self.assertRaises(ValueError):
				google.parse_route(data, 2)

	def test_invalid_json_rejected(self):
		response = Mock(status_code=200)
		response.json.side_effect = ValueError("secret")
		with patch.object(google.requests, "post", return_value=response), self.assertRaises(ValueError):
			google.compute_route(self.points, "secret")

	def test_safe_provider_reasons_explain_configuration_failures(self):
		for reason, message in (
			("API_KEY_INVALID", "Google rejected the API key."),
			("SERVICE_DISABLED", "Enable Routes API"),
			("BILLING_DISABLED", "Enable billing"),
			("API_KEY_HTTP_REFERRER_BLOCKED", "Google rejected the API key restrictions."),
		):
			response = Mock(status_code=400)
			response.json.return_value = {"error": {"message": "secret", "details": [{"reason": reason}]}}
			with self.subTest(reason=reason), patch.object(google.requests, "post", return_value=response):
				with self.assertRaises(ValueError):
					google.compute_route(self.points, "secret")
				shown = google.frappe.throw.call_args.args[0]
				self.assertIn(message, shown)
				self.assertNotIn("secret", shown)

	def test_unrecognized_bad_request_shows_status_not_raw_details(self):
		response = Mock(status_code=400)
		response.json.return_value = {"error": {"message": "secret", "details": [{"reason": "secret"}]}}
		with patch.object(google.requests, "post", return_value=response), self.assertRaises(ValueError):
			google.compute_route(self.points, "secret")
		shown = google.frappe.throw.call_args.args[0]
		self.assertIn("HTTP 400", shown)
		self.assertNotIn("secret", shown)


class TestRoadPreview(unittest.TestCase):
	def setUp(self):
		self.api = inspect.unwrap(routing.preview_road_route)
		self.settings = frappe._dict(enabled=1, routing_provider="Google", enable_google_optimization=1)
		self.settings.get_password = Mock(return_value="server-key")
		self.doc = Mock(
			doctype="Transport Load",
			company="Company",
			docstatus=0,
			movement_type="Customer Delivery",
			source_warehouse="Main",
		)
		self.doc.name = "TL-1"
		self.doc.modified = "2026-11-18 12:00:00.000000"
		self.doc.get = Mock(return_value=None)
		self.doc.delivery_orders = [
			frappe._dict(name="row-1", delivery_address="A"),
			frappe._dict(name="row-2", delivery_address="A"),
			frappe._dict(name="row-3", delivery_address="B"),
		]
		self.enterContext(patch.object(routing, "_", side_effect=lambda value: value))
		self.enterContext(patch.object(routing, "require_authenticated"))
		self.permission = self.enterContext(patch.object(routing, "require_document_permission"))
		self.enterContext(patch.object(routing.frappe, "throw", side_effect=ValueError))
		self.enterContext(patch.object(routing.frappe, "get_roles", return_value=["Transport Dispatcher"]))
		self.get_doc = self.enterContext(patch.object(routing.frappe, "get_doc", side_effect=self.document))
		self.cached = self.enterContext(
			patch.object(routing.frappe, "get_cached_doc", return_value=self.settings)
		)
		self.enterContext(
			patch.object(
				routing,
				"get_address_planning_data",
				side_effect=lambda name: {"latitude": 0, "longitude": len(name)},
			)
		)
		self.enterContext(
			patch.object(
				routing,
				"get_warehouse_planning_data",
				side_effect=lambda name: {"address": name + " Address"},
			)
		)
		self.compute = self.enterContext(
			patch.object(
				routing,
				"compute_ordered_route",
				side_effect=self.route,
			)
		)
		self.pair = self.enterContext(
			patch.object(
				routing,
				"optimize_route",
				side_effect=lambda origin, stops, settings, return_to_start: {
					"distance_meters": 1000,
					"duration_seconds": 60,
					"legs": [{} for _ in range(len(stops) + int(return_to_start))],
					"stop_order": list(reversed(range(len(stops)))),
				},
			)
		)
		self.enterContext(patch.object(routing, "reserve_usage"))
		self.enterContext(
			patch(
				"vuna_transport.services.routing.route_preview_receipt.now_datetime",
				return_value="2026-11-18 16:00:00",
			)
		)
		self.enterContext(
			patch(
				"vuna_transport.services.routing.route_preview_receipt.get_secret",
				return_value="test-only-signing-secret",
			)
		)
		self.trip_loads = self.enterContext(
			patch(
				"vuna_transport.services.execution.lineage_service.get_delivery_trip_loads", return_value=[]
			)
		)
		self.doc.delivery_stops = []
		self.enterContext(
			patch.object(
				routing.trip_estimates,
				"build_trip_manifest",
				return_value={"fingerprint": "trip-inputs", "loads": []},
			)
		)

	def route(self, points, key, **kwargs):
		result = {"distance_meters": 1000, "duration_seconds": 60, "legs": [{} for _ in points[1:]]}
		if kwargs.get("optimize"):
			result["point_order"] = [0, *reversed(range(1, len(points) - 1)), len(points) - 1]
		return result

	def document(self, doctype, name):
		if doctype in routing.SUPPORTED_DOCUMENTS:
			return self.doc
		if doctype == "Address":
			return SimpleNamespace(name=name, address_title=name, links=[])
		if doctype == "Warehouse":
			return SimpleNamespace(name=name, company="Company", disabled=0, is_group=0)
		raise AssertionError(doctype)

	def test_load_deduplicates_destinations_without_saving(self):
		result = self.api("Transport Load", "TL-1")
		points, key = self.compute.call_args.args
		self.assertEqual([p["label"] for p in points], ["Main", "A", "B"])
		self.assertEqual(key, "server-key")
		self.assertEqual(result["legs"][0]["from_label"], "Main")
		self.permission.assert_any_call(self.doc, "write")
		self.doc.save.assert_not_called()
		self.doc.db_set.assert_not_called()

	def test_allocation_preview_orders_all_load_stops_without_persisting_google_metrics(self):
		manifest = {
			"fingerprint": "own-inputs",
			"source_warehouse": "Main",
			"stops": [
				{
					"stop_key": "C1-A",
					"address": "A",
					"customer": "C1",
					"warehouse": None,
					"source_rows": ["r1"],
				},
				{
					"stop_key": "C2-A",
					"address": "A",
					"customer": "C2",
					"warehouse": None,
					"source_rows": ["r2"],
				},
			],
			"loads": [],
		}
		with patch.object(routing.allocation_routes, "build_manifest", return_value=manifest):
			result = self.api("Vehicle Allocation", "VA-1", optimize_stops=1)
		self.assertEqual([r["customer"] for r in result["allocation_route_rows"]], ["C2", "C1"])
		self.assertEqual(result["route_fingerprint"], "own-inputs")
		self.assertEqual(self.pair.call_args.args[0]["label"], "Main")
		self.assertEqual(len(self.pair.call_args.args[1]), 2)
		self.doc.save.assert_not_called()
		self.doc.db_set.assert_not_called()

	def test_trip_preview_uses_load_origin_and_preserves_every_actual_visit(self):
		self.doc.delivery_stops = [
			frappe._dict(name="s1", address="A", vuna_delivery_load="L"),
			frappe._dict(name="s2", address="A", vuna_delivery_load="L"),
		]
		load = frappe._dict(
			company="Company", source_warehouse="Main", movement_type="Customer Delivery", delivery_orders=[]
		)
		self.trip_loads.return_value = [load]
		result = self.api("Delivery Trip", "TRIP", optimize_stops=1)
		self.assertEqual(self.pair.call_args.args[0]["label"], "Main")
		self.assertEqual(result["trip_stop_order"], ["s2", "s1"])
		self.assertTrue(result["route_preview_token"])
		self.doc.save.assert_not_called()

	def test_trip_with_extra_pickup_task_is_not_optimized(self):
		self.doc.delivery_stops = [
			frappe._dict(name="s1", address="A", vuna_delivery_load="L", vuna_task_keys='["return-task"]')
		]
		self.trip_loads.return_value = [
			frappe._dict(company="Company", movement_type="Customer Delivery", delivery_orders=[])
		]
		with self.assertRaises(ValueError):
			self.api("Delivery Trip", "TRIP", optimize_stops=1)
		self.assertIn("dependency-aware", routing.frappe.throw.call_args.args[0])
		self.pair.assert_not_called()

	def test_trip_preserves_repeated_visits_and_optional_return(self):
		self.doc.driver_address = "Driver"
		self.doc.delivery_stops = [frappe._dict(address=name) for name in ("A", "B", "A")]
		self.api("Delivery Trip", "TRIP-1", return_to_start=1)
		self.assertEqual(
			[p["label"] for p in self.compute.call_args.args[0]], ["Driver", "A", "B", "A", "Driver"]
		)

	def test_supplier_load_starts_at_supplier_and_ends_at_warehouse(self):
		self.doc.movement_type = "Supplier Pickup"
		self.doc.source_address = "Supplier"
		self.doc.delivery_orders = [frappe._dict(destination_warehouse="Depot")]
		self.api("Transport Load", "TL-1")
		self.assertEqual([p["label"] for p in self.compute.call_args.args[0]], ["Supplier", "Depot"])

	def test_warehouse_origin_and_google_settings_fallback(self):
		self.settings.get_password.return_value = None
		self.cached.side_effect = [self.settings, frappe._dict(api_key="existing-key")]
		self.api("Transport Load", "TL-1")
		self.assertEqual(self.compute.call_args.args[0][0]["label"], "Main")
		self.assertEqual(self.compute.call_args.args[1], "existing-key")

	def test_load_origin_cannot_be_overridden(self):
		with self.assertRaises(TypeError):
			self.api("Transport Load", "TL-1", start_address="Other")
		self.compute.assert_not_called()

	def test_missing_source_warehouse_requires_fixing_load(self):
		self.doc.source_warehouse = None
		with self.assertRaises(ValueError):
			self.api("Transport Load", "TL-1")
		routing.frappe.throw.assert_called_once_with(
			"Select a Source Warehouse on the Transport Load before previewing the route."
		)
		self.compute.assert_not_called()

	def test_unsupported_document_rejected_before_loading(self):
		with self.assertRaises(ValueError):
			self.api("User", "Administrator")
		self.get_doc.assert_not_called()
		self.compute.assert_not_called()

	def test_role_and_document_permission_required(self):
		with patch.object(routing.frappe, "get_roles", return_value=["Guest"]), self.assertRaises(ValueError):
			self.api("Transport Load", "TL-1")
		self.get_doc.assert_not_called()
		self.permission.side_effect = ValueError("No permission")
		with self.assertRaises(ValueError):
			self.api("Transport Load", "TL-1")
		self.compute.assert_not_called()

	def test_disabled_and_cancelled_rejected(self):
		for enabled, provider, status in ((0, "Google", 0), (1, "Geographic Estimate", 0), (1, "Google", 2)):
			self.settings.enabled = enabled
			self.settings.routing_provider = provider
			self.doc.docstatus = status
			with (
				self.subTest(enabled=enabled, provider=provider, status=status),
				self.assertRaises(ValueError),
			):
				self.api("Transport Load", "TL-1")
		self.compute.assert_not_called()

	def test_missing_key_rejected(self):
		self.settings.get_password.return_value = None
		self.cached.side_effect = [self.settings, frappe._dict()]
		with self.assertRaises(ValueError):
			self.api("Transport Load", "TL-1")
		self.compute.assert_not_called()

	def test_foreign_company_and_inactive_warehouse_rejected(self):
		for company, disabled, is_group in (("Other", 0, 0), ("Company", 1, 0), ("Company", 0, 1)):
			with self.subTest(company=company, disabled=disabled, is_group=is_group):
				self.get_doc.side_effect = (
					lambda doctype, name: self.doc
					if doctype == "Transport Load"
					else SimpleNamespace(company=company, disabled=disabled, is_group=is_group)
				)
				with self.assertRaises(ValueError):
					self.api("Transport Load", "TL-1")
		self.compute.assert_not_called()

	def test_address_linked_to_foreign_company_is_rejected(self):
		self.get_doc.side_effect = lambda doctype, name: SimpleNamespace(
			links=[frappe._dict(link_doctype="Company", link_name="Other")]
		)
		with self.assertRaises(ValueError):
			routing._address_point("Foreign", "Company")
		self.compute.assert_not_called()

	def test_centroid_is_not_a_valid_address_endpoint(self):
		with (
			patch.object(
				routing,
				"get_address_planning_data",
				return_value={"planning_latitude": 1, "planning_longitude": 2},
			),
			self.assertRaises(ValueError),
		):
			self.api("Transport Load", "TL-1")
		self.compute.assert_not_called()

	def test_point_validation_accepts_zero_not_invalid_coordinates(self):
		self.assertEqual(routing._point(0, 0, "Origin")["latitude"], 0)
		for latitude, longitude in ((None, 1), (True, 1), (91, 1), (0, 181), (float("nan"), 0)):
			with self.subTest(latitude=latitude, longitude=longitude), self.assertRaises(ValueError):
				routing._point(latitude, longitude, "Address")

	def test_placeholder_address_is_rejected_before_billable_request(self):
		with patch.object(routing, "get_address_planning_data", return_value={"latitude": 0, "longitude": 0}):
			with self.assertRaises(ValueError):
				self.api("Transport Load", "TL-1")
		self.compute.assert_not_called()
		self.assertIn("placeholder coordinates", routing.frappe.throw.call_args.args[0])

	def test_oversized_trip_is_not_silently_truncated(self):
		self.doc.driver_address = "Driver"
		self.doc.delivery_stops = [frappe._dict(address="A") for _ in range(101)]
		with self.assertRaises(ValueError):
			self.api("Delivery Trip", "TRIP-1")
		self.compute.assert_not_called()

	def test_round_trip_optimization_reorders_destinations_and_preserves_load_rows(self):
		result = self.api("Transport Load", "TL-1", return_to_start=1, optimize_stops=1)
		self.assertEqual(result["load_row_order"], ["row-3", "row-1", "row-2"])
		self.assertEqual(result["legs"][0]["to_label"], "B")
		self.assertEqual(result["legs"][-1]["to_label"], "Main")
		self.assertEqual(result["source_modified"], self.doc.modified)
		self.doc.save.assert_not_called()

	def test_two_locations_keep_three_customer_deliveries_and_need_no_return(self):
		self.doc.delivery_orders = [
			frappe._dict(name="row-1", delivery_address="A", customer="BKN"),
			frappe._dict(name="row-2", delivery_address="B", customer="BKN"),
			frappe._dict(name="row-3", delivery_address="B", customer="Wephukulu"),
		]
		result = self.api("Transport Load", "TL-1", optimize_stops=1)
		self.assertEqual(result["load_row_order"], ["row-2", "row-3", "row-1"])
		self.assertEqual([leg["to_label"] for leg in result["legs"]], ["B", "A"])
		self.assertEqual(len(self.doc.delivery_orders), 3)
		self.pair.assert_called_once()
		self.compute.assert_not_called()
		self.doc.save.assert_not_called()

	def test_managed_open_route_can_choose_a_new_final_destination(self):
		self.doc.delivery_orders.append(frappe._dict(name="row-4", delivery_address="C"))
		result = self.api("Transport Load", "TL-1", optimize_stops=1)
		self.assertEqual(result["load_row_order"], ["row-4", "row-3", "row-1", "row-2"])
		self.assertEqual(result["legs"][-1]["to_label"], "A")

	def test_forty_location_optimization_is_not_limited_by_compute_routes_waypoints(self):
		self.doc.delivery_orders = [
			frappe._dict(name=f"row-{index}", delivery_address=f"Address {index}") for index in range(40)
		]
		result = self.api("Transport Load", "TL-1", optimize_stops=1)
		self.assertEqual(len(result["load_row_order"]), 40)
		self.assertEqual(len(result["legs"]), 40)
		self.assertEqual(result["load_row_order"][0], "row-39")

	def test_optimizer_disabled_and_configured_role_denial_make_no_provider_call(self):
		self.settings.enable_google_optimization = 0
		with self.assertRaises(ValueError):
			self.api("Transport Load", "TL-1", optimize_stops=1)
		self.settings.routing_allowed_roles = [frappe._dict(role="Transport Manager")]
		with self.assertRaises(ValueError):
			self.api("Transport Load", "TL-1")
		self.compute.assert_not_called()
		self.pair.assert_not_called()

	def test_depot_optimization_groups_rows_by_receiving_warehouse(self):
		self.doc.movement_type = "Warehouse Transfer"
		for row in self.doc.delivery_orders:
			row.destination_warehouse = row.delivery_address
		result = self.api("Transport Load", "TL-1", return_to_start=1, optimize_stops=1)
		self.assertEqual(result["load_row_order"], ["row-3", "row-1", "row-2"])
		self.assertEqual(result["legs"][0]["to_label"], "B")

	def test_saved_demand_constraints_are_checked_even_if_master_changed(self):
		self.doc.delivery_orders[0].transport_demand = "TD-1"
		for values in (
			{"company": "Company", "source_document_type": "Sales Order", "delivery_from_time": "08:00:00"},
			{"company": "Company", "source_document_type": "Return Request"},
			{"company": "Other", "source_document_type": "Sales Order"},
		):
			self.get_doc.side_effect = (
				lambda dt, name: frappe._dict(values) if dt == "Transport Demand" else self.document(dt, name)
			)
			with self.subTest(values=values), self.assertRaises(ValueError):
				self.api("Transport Load", "TL-1", return_to_start=1, optimize_stops=1)
		self.compute.assert_not_called()

	def test_allocated_load_cannot_be_optimized_even_while_draft(self):
		self.doc.get.side_effect = lambda field: "VA-1" if field == "active_vehicle_allocation" else None
		with self.assertRaises(ValueError):
			self.api("Transport Load", "TL-1", return_to_start=1, optimize_stops=1)
		self.compute.assert_not_called()

	def test_optimization_rejects_execution_and_pickup_dependencies(self):
		for doctype, status, movement in (
			("Delivery Trip", 0, "Customer Delivery"),
			("Transport Load", 1, "Customer Delivery"),
			("Transport Load", 0, "Supplier Pickup"),
			("Transport Load", 0, "Return Pickup"),
		):
			self.doc.docstatus, self.doc.movement_type = status, movement
			with (
				self.subTest(doctype=doctype, status=status, movement=movement),
				self.assertRaises(ValueError),
			):
				self.api(doctype, "TL-1", optimize_stops=1)
		self.compute.assert_not_called()

	def test_optimization_rejects_constraints_and_too_few_stops(self):
		with (
			patch.object(
				routing, "get_address_planning_data", return_value={"delivery_from_time": "08:00:00"}
			),
			self.assertRaises(ValueError),
		):
			self.api("Transport Load", "TL-1", optimize_stops=1)
		self.doc.delivery_orders = self.doc.delivery_orders[:2]
		with self.assertRaises(ValueError):
			self.api("Transport Load", "TL-1", optimize_stops=1)
		self.compute.assert_not_called()
		self.pair.assert_not_called()
