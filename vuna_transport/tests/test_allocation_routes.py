"""Operational route approval, trip inheritance and shared journey costing."""

import copy
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

import frappe

from vuna_transport.services.costing import transport_cost_estimation_service as costing
from vuna_transport.services.routing import allocation_route_service as routes
from vuna_transport.services.routing import route_preview_receipt as receipts
from vuna_transport.vuna_transport.doctype.vehicle_allocation import vehicle_allocation as controller


class Allocation(frappe._dict):
	def set(self, key, value):
		self[key] = value

	def get_doc_before_save(self):
		return self.get("previous")


class TestAllocationRoutes(unittest.TestCase):
	def setUp(self):
		self.enterContext(patch.object(routes, "_", side_effect=lambda value: value))
		self.enterContext(patch.object(routes.frappe, "throw", side_effect=ValueError))
		self.enterContext(patch.object(routes, "require_document_permission"))
		self.enterContext(patch.object(routes, "require_routing_role"))
		self.enterContext(patch.object(receipts, "get_secret", return_value="test-only-signing-secret"))
		self.enterContext(patch.object(receipts, "now_datetime", return_value="2026-11-18 16:00:00"))
		self.enterContext(
			patch.object(
				routes,
				"get_address_planning_data",
				side_effect=lambda name: {"latitude": 1, "longitude": 2, "address": name},
			)
		)
		self.enterContext(
			patch.object(
				routes, "get_warehouse_planning_data", side_effect=lambda name: {"address": name + " Address"}
			)
		)
		self.enterContext(patch.object(routes.frappe, "get_doc", side_effect=self.get_doc))
		self.vehicle = frappe._dict(name="V", company="Company")
		self.loads = [
			frappe._dict(
				name="L1",
				company="Company",
				docstatus=1,
				movement_type="Customer Delivery",
				source_warehouse="Main",
				delivery_orders=[
					frappe._dict(
						name="r1",
						customer="C1",
						delivery_address="Shared",
						item_code="Milk",
						allocated_quantity=2,
						uom="Carton",
						conversion_factor=12,
					)
				],
			),
			frappe._dict(
				name="L2",
				company="Company",
				docstatus=1,
				movement_type="Customer Delivery",
				source_warehouse="Main",
				delivery_orders=[
					frappe._dict(
						name="r2",
						customer="C2",
						delivery_address="Shared",
						item_code="Milk",
						allocated_quantity=3,
						uom="Carton",
						conversion_factor=12,
					)
				],
			),
		]
		self.enterContext(patch.object(routes, "get_allocation_loads", return_value=self.loads))
		self.allocation = Allocation(
			company="Company",
			vehicle="V",
			driver="D",
			cost_structure="Costs",
			planned_reporting_time="2026-11-18 09:00:00",
			docstatus=0,
			flags=frappe._dict(),
		)

	def get_doc(self, doctype, name):
		if doctype == "Vehicle":
			return self.vehicle
		if doctype == "Warehouse":
			return frappe._dict(name=name, company="Company", disabled=0, is_group=0)
		if doctype == "Delivery Note":
			return frappe._dict(
				items=[frappe._dict(vuna_delivery_load_order="r1" if name == "DN1" else "r2")]
			)
		return frappe._dict(name=name)

	def prepare(self):
		manifest = routes.build_manifest(self.allocation)
		self.allocation.route_stops = [frappe._dict(row) for row in routes.route_rows(manifest["stops"])]
		self.allocation.route_manifest_fingerprint = manifest["fingerprint"]
		return manifest

	def approve(self):
		self.prepare()
		self.allocation.flags.approve_route = True
		routes.validate_route(self.allocation)
		self.allocation.flags.approve_route = False
		self.allocation.previous = copy.deepcopy(self.allocation)

	def apply_distance(self, meters=150000):
		manifest = self.prepare()
		self.allocation.name = "VA"
		self.allocation.modified = "2026-11-18 16:00:00.000001"
		self.allocation.previous = copy.deepcopy(self.allocation)
		keys = [row.stop_key for row in self.allocation.route_stops]
		self.allocation.route_preview_token = receipts.issue_receipt(
			self.allocation, manifest["fingerprint"], keys, meters
		)
		return manifest, keys

	def test_verified_google_distance_is_retained_and_cannot_be_edited_in_browser(self):
		self.apply_distance()
		routes.validate_route(self.allocation)
		self.assertEqual(self.allocation.route_distance_km, 150)
		self.assertEqual(self.allocation.route_distance_provider, "Google")
		self.assertTrue(self.allocation.route_calculated_on)
		self.assertIsNone(self.allocation.route_preview_token)
		self.allocation.previous = copy.deepcopy(self.allocation)
		self.allocation.route_distance_km = 999
		routes.validate_route(self.allocation)
		self.assertEqual(self.allocation.route_distance_km, 150)

	def test_reordered_stops_clear_retained_distance_and_costs(self):
		self.apply_distance()
		routes.validate_route(self.allocation)
		self.allocation.previous = copy.deepcopy(self.allocation)
		self.allocation.route_stops.reverse()
		routes.validate_route(self.allocation)
		self.assertIsNone(self.allocation.route_calculated_on)
		self.assertEqual(self.allocation.route_distance_km, 0)
		self.assertEqual(self.allocation.estimated_marginal_cost, 0)

	def test_changed_load_inputs_clear_retained_distance(self):
		self.apply_distance()
		routes.validate_route(self.allocation)
		self.allocation.previous = copy.deepcopy(self.allocation)
		self.loads[0].delivery_orders[0].allocated_quantity = 50
		routes.validate_route(self.allocation)
		self.assertEqual(self.allocation.route_status, "Outdated")
		self.assertIsNone(self.allocation.route_calculated_on)

	def test_tampered_expired_or_stale_preview_receipts_are_rejected(self):
		manifest, keys = self.apply_distance()
		token = self.allocation.route_preview_token
		envelope = json.loads(token)
		envelope["payload"]["distance_km"] = 999
		with self.assertRaises(ValueError):
			receipts.verify_receipt(
				json.dumps(envelope), self.allocation, self.allocation.previous, manifest["fingerprint"], keys
			)
		with patch.object(receipts.time, "time", return_value=envelope["payload"]["issued"] + 1801):
			with self.assertRaises(ValueError):
				receipts.verify_receipt(
					token, self.allocation, self.allocation.previous, manifest["fingerprint"], keys
				)
		with self.assertRaises(ValueError):
			receipts.verify_receipt(
				token,
				self.allocation,
				self.allocation.previous,
				manifest["fingerprint"],
				list(reversed(keys)),
			)
		self.allocation.previous.modified = "changed"
		with self.assertRaises(ValueError):
			receipts.verify_receipt(
				token, self.allocation, self.allocation.previous, manifest["fingerprint"], keys
			)

	def test_zero_road_distance_is_a_valid_retained_result(self):
		self.apply_distance(meters=0)
		routes.validate_route(self.allocation)
		self.assertEqual(self.allocation.route_distance_km, 0)
		self.assertTrue(self.allocation.route_calculated_on)

	def test_shared_address_keeps_customers_and_all_source_rows(self):
		manifest = self.prepare()
		self.assertEqual(len(manifest["stops"]), 2)
		self.assertNotEqual(manifest["stops"][0]["stop_key"], manifest["stops"][1]["stop_key"])
		self.assertEqual([s["source_rows"] for s in manifest["stops"]], [["r1"], ["r2"]])

	def test_timestamp_serialization_does_not_invalidate_an_unchanged_manifest(self):
		manifest = self.prepare()
		self.allocation.planned_reporting_time = "2026-11-18 09:00:00.000000"
		self.assertEqual(routes.build_manifest(self.allocation)["fingerprint"], manifest["fingerprint"])

	def test_approval_is_server_owned_and_unchanged_save_preserves_it(self):
		self.prepare()
		self.allocation.route_status = "Approved"
		routes.validate_route(self.allocation)
		self.assertEqual(self.allocation.route_status, "Proposed")
		self.approve()
		approved_on = self.allocation.route_approved_on
		routes.validate_route(self.allocation)
		self.assertEqual(self.allocation.route_status, "Approved")
		self.assertEqual(self.allocation.route_approved_on, approved_on)

	def test_changed_quantity_vehicle_or_return_invalidates_approval(self):
		self.approve()
		self.loads[0].delivery_orders[0].allocated_quantity = 5
		routes.validate_route(self.allocation)
		self.assertEqual(self.allocation.route_status, "Outdated")
		self.assertIsNone(self.allocation.route_approved_by)
		self.allocation.docstatus = 1
		with self.assertRaises(ValueError):
			routes.require_approved_route(self.allocation)
		with self.assertRaises(ValueError):
			routes.validate_route(self.allocation)

	def test_row_reorder_requires_new_approval(self):
		self.approve()
		self.allocation.route_stops.reverse()
		routes.validate_route(self.allocation)
		self.assertEqual(self.allocation.route_status, "Proposed")

	def test_changed_destinations_can_save_before_refreshing_outdated_stops(self):
		self.approve()
		old_key = self.allocation.route_stops[0].stop_key
		self.loads[0].delivery_orders[0].delivery_address = "New Address"
		routes.validate_route(self.allocation)
		self.assertEqual(self.allocation.route_status, "Outdated")
		self.assertEqual(self.allocation.route_stops[0]["stop_key"], old_key)
		self.assertIsNone(self.allocation.route_approved_by)

	def test_child_references_are_rebuilt_and_missing_stops_rejected(self):
		self.prepare()
		self.allocation.route_stops[0].customer = "Injected"
		routes.validate_route(self.allocation)
		self.assertEqual(self.allocation.route_stops[0].customer, "C1")
		self.allocation.route_stops.pop()
		with self.assertRaises(ValueError):
			routes.validate_route(self.allocation)

	def test_multiple_origins_supplier_pickups_and_returns_cannot_be_optimized(self):
		self.loads[1].source_warehouse = "Other"
		with self.assertRaises(ValueError):
			self.prepare()
		self.loads[1].source_warehouse = "Main"
		self.loads[1].movement_type = "Supplier Pickup"
		with self.assertRaises(ValueError):
			self.prepare()

	def test_trip_inherits_route_not_delivery_note_creation_order(self):
		self.prepare()
		self.allocation.route_stops.reverse()
		self.allocation.flags.approve_route = True
		routes.validate_route(self.allocation)
		trip = frappe._dict(
			delivery_stops=[frappe._dict(delivery_note="DN1"), frappe._dict(delivery_note="DN2")]
		)
		routes.order_trip_stops(trip, self.allocation, self.loads)
		self.assertEqual([s.delivery_note for s in trip.delivery_stops], ["DN2", "DN1"])
		self.assertEqual([s.idx for s in trip.delivery_stops], [1, 2])

	def test_load_order_fallback_and_pickup_dependency_are_preserved(self):
		trip = frappe._dict(
			delivery_stops=[frappe._dict(delivery_note="DN2"), frappe._dict(delivery_note="DN1")]
		)
		routes.order_trip_stops(trip, None, self.loads)
		self.assertEqual(trip.delivery_stops[0].delivery_note, "DN1")
		trip.delivery_stops.reverse()
		routes.order_trip_stops(
			trip, None, self.loads, [{"task_key": "pickup", "task_type": "Return Pickup"}]
		)
		self.assertEqual(trip.delivery_stops[0].delivery_note, "DN2")

	def test_depot_receipt_tasks_inherit_the_approved_combined_order(self):
		for index, load in enumerate(self.loads, 1):
			load.movement_type = "Warehouse Transfer"
			load.delivery_orders[0].destination_warehouse = "Depot" + str(index)
		self.prepare()
		self.allocation.route_stops.reverse()
		self.allocation.flags.approve_route = True
		routes.validate_route(self.allocation)
		trip = frappe._dict(
			delivery_stops=[frappe._dict(vuna_task_keys='["T1"]'), frappe._dict(vuna_task_keys='["T2"]')]
		)
		specs = [
			{"task_key": "T1", "task_type": "Transfer Receipt", "items": [{"load_item": "r1"}]},
			{"task_key": "T2", "task_type": "Transfer Receipt", "items": [{"load_item": "r2"}]},
		]
		routes.order_trip_stops(trip, self.allocation, self.loads, specs)
		self.assertEqual(trip.delivery_stops[0].vuna_task_keys, '["T2"]')

	def test_changed_review_timestamp_cannot_approve_another_users_order(self):
		self.prepare()
		self.allocation.modified = "2026-11-18 15:00:01.000000"
		with (
			patch.object(routes.frappe.db, "get_value"),
			patch.object(routes.frappe, "get_doc", return_value=self.allocation),
		):
			with self.assertRaises(ValueError):
				routes.approve_route(
					"VA", self.allocation.route_manifest_fingerprint, "2026-11-18 15:00:00.000000"
				)
			self.assertIn("changed after your review", routes.frappe.throw.call_args.args[0])


class TestJourneyCosting(unittest.TestCase):
	def test_snapshot_uses_retained_road_distance_without_manual_input(self):
		allocation = frappe._dict(
			vehicle="V",
			route_stops=[1],
			route_calculated_on="2026-11-18 16:00:00",
			route_distance_km=150,
			planned_reporting_time="2026-11-18 16:00:00",
		)
		with (
			patch.object(controller.frappe.db, "get_single_value", return_value=1),
			patch.object(controller, "get_allocation_load_names", return_value=["L1", "L2"]),
			patch.object(
				costing,
				"estimate_transport_journey",
				return_value={
					"distance": 150,
					"distance_uom": "Kilometer",
					"distance_source": "Routing Provider",
					"marginal_cost": 1500,
					"components": [],
					"warnings": [],
				},
			) as estimate,
		):
			controller.snapshot_transport_cost(allocation)
		self.assertEqual(estimate.call_args.kwargs["distance"], 150)
		self.assertEqual(estimate.call_args.kwargs["distance_source"], "Routing Provider")
		self.assertEqual(allocation.estimated_distance, 150)
		self.assertEqual(allocation.estimated_marginal_cost, 1500)

	def test_destination_tariffs_keep_context_without_repeating_shared_destinations(self):
		vehicle = frappe._dict(name="V", company="Company")
		loads = [frappe._dict(name=name, company="Company") for name in ("L1", "L2")]
		a, b = [frappe._dict(cost_key=name) for name in ("A", "B")]
		context = costing.CostContext(
			company="Company",
			load=loads[0],
			vehicle=vehicle,
			date="2026-11-18",
			distance=0,
			distance_uom="Kilometer",
			distance_source="Planned Distance",
			source_warehouse="Main",
			destination="A",
			destinations=["A"],
			destination_geographies=[a],
		)
		contexts = [
			context,
			replace(
				context,
				load=loads[1],
				destination=None,
				destinations=["A", "B"],
				destination_geographies=[a, b],
			),
		]
		profile = frappe._dict(
			name="Rates",
			cost_structure="Costs",
			currency="KES",
			components=[
				frappe._dict(
					cost_component="Destination", calculation_basis="Destination Rate", cost_nature="Variable"
				)
			],
			destination_rates=[
				frappe._dict(destination_key="A", source_warehouse="Main", rate=50),
				frappe._dict(destination_key="B", source_warehouse="Main", rate=80),
			],
		)
		with (
			patch.object(costing, "_resolve_document", side_effect=lambda doctype, value: value),
			patch.object(costing, "require_document_permission"),
			patch.object(costing, "require_company_access"),
			patch.object(costing, "get_transport_uoms"),
			patch.object(costing, "_build_context", side_effect=contexts),
			patch.object(costing, "get_applicable_cost_structure_assignment", return_value=profile),
		):
			result = costing.estimate_transport_journey(loads, vehicle, distance=100)
			with patch.object(costing, "_build_context", side_effect=contexts):
				revised_trip = costing.estimate_transport_journey(
					loads, vehicle, distance=100, journey_destinations=[b]
				)
		self.assertEqual(result["marginal_cost"], 130)
		self.assertEqual(revised_trip["marginal_cost"], 80)
		self.assertEqual(len(result["components"]), 2)

	def test_explicit_zero_distance_does_not_trigger_geographic_fallback(self):
		with (
			patch.object(costing, "get_load_planning_locations", return_value=(None, [])),
			patch.object(costing, "estimate_delivery_load_distance") as geographic,
		):
			result = costing._build_context(
				frappe._dict(company="Company"),
				frappe._dict(),
				frappe._dict(distance_uom="Kilometer"),
				as_of_date="2026-11-18",
				distance=0,
				distance_source="Routing Provider",
			)
		self.assertEqual(result.distance, 0)
		geographic.assert_not_called()

	def test_shared_distance_fuel_flat_and_monthly_charges_are_counted_once(self):
		vehicle = frappe._dict(name="V", company="Company", vuna_expected_fuel_efficiency=5, uom="Kilometer")
		loads = [frappe._dict(name=name, company="Company") for name in ("L1", "L2")]
		context = costing.CostContext(
			company="Company",
			load=loads[0],
			vehicle=vehicle,
			date="2026-11-18",
			distance=0,
			distance_uom="Kilometer",
			distance_source="Planned Distance",
			destination=None,
			destinations=[],
		)

		def component(basis, **values):
			return frappe._dict(
				cost_component=basis, calculation_basis=basis, cost_nature="Variable", **values
			)

		profile = frappe._dict(
			name="Rates",
			cost_structure="Costs",
			currency="KES",
			components=[
				component("Flat Per Trip", flat_amount=100),
				component("Per Distance", rate=2, rate_uom="Kilometer"),
				component("Per Fuel Quantity", rate=10, rate_uom="Litre"),
				component("Monthly Fixed", flat_amount=500),
			],
		)
		with (
			patch.object(costing, "_resolve_document", side_effect=lambda doctype, value: value),
			patch.object(costing, "require_document_permission"),
			patch.object(costing, "require_company_access"),
			patch.object(costing, "get_transport_uoms"),
			patch.object(costing, "_build_context", return_value=context),
			patch.object(costing, "get_applicable_cost_structure_assignment", return_value=profile),
		):
			result = costing.estimate_transport_journey(loads, vehicle, distance=100)
		self.assertEqual(result["distance"], 100)
		self.assertEqual(result["marginal_cost"], 500)
		self.assertEqual(result["fixed_cost"], 500)
		self.assertEqual(len(result["components"]), 4)

	def test_invalid_distance_never_resolves_documents(self):
		with (
			patch.object(costing, "_resolve_document", return_value=frappe._dict()),
			patch.object(costing.frappe, "throw", side_effect=ValueError),
		):
			for value in (-1, float("nan"), float("inf"), "bad"):
				with self.subTest(value=value), self.assertRaises(ValueError):
					costing.estimate_transport_journey(["L"], "V", distance=value)
