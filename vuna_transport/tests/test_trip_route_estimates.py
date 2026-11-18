"""Applied trip routes reprice automatically without changing allocation estimates."""

import copy
import unittest
from unittest.mock import patch

import frappe

from vuna_transport.services.routing import road_routing_service as routing
from vuna_transport.services.routing import route_preview_receipt as receipts
from vuna_transport.services.routing import trip_route_estimate_service as estimates
from vuna_transport.services.routing.trip_route_estimate_service import build_trip_manifest


class Trip(frappe._dict):
	def set(self, field, value):
		self[field] = value

	def get_doc_before_save(self):
		return self.get("previous")


class TestTripRouteEstimates(unittest.TestCase):
	def setUp(self):
		self.doc = Trip(
			doctype="Delivery Trip",
			name="DT",
			company="Company",
			vehicle="Vehicle",
			driver="Driver",
			departure_time="2026-11-18 16:00:00",
			modified="2026-11-18 16:00:00.000001",
			docstatus=0,
			vuna_vehicle_allocation="VA",
			delivery_stops=[frappe._dict(name="s1", address="A"), frappe._dict(name="s2", address="B")],
		)
		self.manifest = {"fingerprint": "trip-inputs", "loads": ["TL1", "TL2"]}
		self.settings = frappe._dict(enable_transport_costing=1)
		self.enterContext(patch.object(estimates, "build_trip_manifest", return_value=self.manifest))
		self.enterContext(patch.object(estimates.frappe, "get_cached_doc", return_value=self.settings))
		self.enterContext(patch.object(estimates.frappe, "throw", side_effect=ValueError))
		self.enterContext(patch.object(receipts, "get_secret", return_value="test-signing-secret"))
		self.enterContext(patch.object(receipts, "now_datetime", return_value="2026-11-18 16:00:00"))
		self.guard = self.enterContext(patch.object(routing, "_validate_optimization"))
		self.enterContext(
			patch("vuna_transport.services.routing.routing_policy_service.require_routing_role")
		)
		self.price = self.enterContext(
			patch.object(
				estimates,
				"estimate_transport_journey",
				return_value={
					"currency": "KES",
					"cost_structure": "Fleet Rates",
					"cost_structure_assignment": "Rates",
					"marginal_cost": 1500,
					"fixed_cost": 500,
					"estimated_full_cost": 1500,
					"components": [{"component": "Fuel", "amount": 1500}],
					"warnings": [],
				},
			)
		)

	def apply(self, meters=150000):
		self.doc.previous = copy.deepcopy(self.doc)
		self.doc.vuna_route_preview_token = receipts.issue_receipt(
			self.doc, self.manifest["fingerprint"], [stop.name for stop in self.doc.delivery_stops], meters
		)
		estimates.validate_trip_estimate(self.doc)

	def test_apply_saves_distance_and_recalculates_cost_without_writing_allocation(self):
		with patch.object(estimates.frappe.db, "set_value") as write:
			self.apply()
			write.assert_not_called()
		self.assertEqual(self.doc.vuna_route_distance_km, 150)
		self.assertEqual(self.doc.vuna_route_marginal_cost, 1500)
		self.assertEqual(self.doc.vuna_route_estimate_status, "Current")
		self.assertIsNone(self.doc.vuna_route_preview_token)
		self.assertEqual(self.price.call_args.args, (["TL1", "TL2"], "Vehicle"))
		self.assertEqual(self.price.call_args.kwargs["distance"], 150)
		self.guard.assert_called_once_with(self.doc, "Delivery Trip")

	def test_trip_manifest_keeps_order_out_of_inputs_and_uses_actual_stop_geographies(self):
		with (
			patch.object(estimates, "build_manifest", return_value={"fingerprint": "allocation-inputs"}),
			patch.object(estimates, "require_document_permission"),
			patch.object(estimates.frappe, "get_doc", return_value=frappe._dict()),
			patch.object(
				estimates,
				"get_address_planning_data",
				side_effect=lambda name: {"address": name, "territory": name + " region"},
			),
		):
			before = build_trip_manifest(self.doc, loads=["TL1", "TL2"])
			self.doc.delivery_stops.reverse()
			after = build_trip_manifest(self.doc, loads=["TL1", "TL2"])
			self.assertEqual(before["fingerprint"], after["fingerprint"])
			self.assertEqual({row.cost_key for row in after["destinations"]}, {"A", "B"})
			self.doc.delivery_stops[0].address = "Changed address"
			changed = build_trip_manifest(self.doc, loads=["TL1", "TL2"])
			self.assertNotEqual(before["fingerprint"], changed["fingerprint"])

	def test_unchanged_save_preserves_snapshot_and_ignores_client_costs(self):
		self.apply()
		self.doc.previous = copy.deepcopy(self.doc)
		self.doc.vuna_route_distance_km = 999
		self.doc.vuna_route_marginal_cost = 1
		estimates.validate_trip_estimate(self.doc)
		self.assertEqual(self.doc.vuna_route_distance_km, 150)
		self.assertEqual(self.doc.vuna_route_marginal_cost, 1500)
		self.price.assert_called_once()

	def test_manual_stop_reorder_invalidates_estimate_without_google_or_repricing(self):
		self.apply()
		self.doc.previous = copy.deepcopy(self.doc)
		self.doc.delivery_stops.reverse()
		estimates.validate_trip_estimate(self.doc)
		self.assertEqual(self.doc.vuna_route_estimate_status, "Outdated")
		self.assertIsNone(self.doc.vuna_route_distance_km)
		self.assertIsNone(self.doc.vuna_route_marginal_cost)
		self.price.assert_called_once()

	def test_changed_inputs_invalidate_distance_and_costs(self):
		self.apply()
		self.doc.previous = copy.deepcopy(self.doc)
		self.manifest["fingerprint"] = "changed-inputs"
		estimates.validate_trip_estimate(self.doc)
		self.assertEqual(self.doc.vuna_route_estimate_status, "Outdated")
		self.price.assert_called_once()

	def test_new_applied_route_automatically_replaces_trip_estimate(self):
		self.apply()
		self.doc.delivery_stops.reverse()
		self.doc.modified = "2026-11-18 16:01:00.000002"
		self.price.return_value["marginal_cost"] = 2000
		self.apply(meters=200000)
		self.assertEqual(self.doc.vuna_route_distance_km, 200)
		self.assertEqual(self.doc.vuna_route_marginal_cost, 2000)
		self.assertEqual(self.price.call_count, 2)

	def test_disabled_costing_retains_distance_without_presenting_zero_as_a_price(self):
		self.settings.enable_transport_costing = 0
		self.apply()
		self.assertEqual(self.doc.vuna_route_estimate_status, "Distance Only")
		self.assertEqual(self.doc.vuna_route_distance_km, 150)
		self.assertIsNone(self.doc.vuna_route_marginal_cost)
		self.price.assert_not_called()

	def test_missing_assignment_is_marked_for_review(self):
		self.price.return_value["cost_structure_assignment"] = None
		self.apply()
		self.assertEqual(self.doc.vuna_route_estimate_status, "Needs Review")

	def test_zero_distance_is_not_replaced_with_a_geographic_estimate(self):
		self.apply(meters=0)
		self.assertEqual(self.price.call_args.kwargs["distance"], 0)

	def test_started_or_submitted_trip_cannot_apply_a_receipt(self):
		for field, value in (("docstatus", 1), ("vuna_actual_start", "2026-11-18 16:00:00")):
			with self.subTest(field=field), self.assertRaises(ValueError):
				self.doc[field] = value
				self.apply()
			self.doc[field] = 0 if field == "docstatus" else None
		self.price.assert_not_called()

	def test_signed_distance_cannot_be_reused_for_an_allocation_with_the_same_name(self):
		self.doc.previous = copy.deepcopy(self.doc)
		token = receipts.issue_receipt(self.doc, "trip-inputs", ["s1", "s2"], 1000)
		self.doc.doctype = "Vehicle Allocation"
		with self.assertRaises(ValueError):
			receipts.verify_receipt(token, self.doc, self.doc.previous, "trip-inputs", ["s1", "s2"])
