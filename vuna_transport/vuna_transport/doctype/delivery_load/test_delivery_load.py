# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

from frappe.exceptions import ValidationError
from frappe.tests import UnitTestCase

from vuna_transport.services.planning.delivery_load_service import (
	_fits,
	apply_capacity_buffer,
	calculate_load_totals,
)
from vuna_transport.services.integrations.erpnext_integration import apply_allocated_delivery_quantities
from vuna_transport.services.execution.execution_service import _get_next_action
from vuna_transport.services.execution.dispatch_guard_service import is_load_status_transition_allowed
from vuna_transport.services.execution.lineage_service import (
	validate_delivery_note_item_allocations,
	validate_delivery_trip_note_set,
)
from vuna_transport.services.planning.load_validation_service import calculate_allocated_measurements
from vuna_transport.services.routing.geography_service import (
	calculate_delivery_window_compatibility,
	calculate_geographic_cohesion,
)
from vuna_transport.services.fleet.vehicle_category_service import (
	evaluate_vehicle_category,
	maximum_fitting_fraction,
)
from vuna_transport.services.shared.workflow_policy_service import (
	ALLOCATION_AFTER_DELIVERY_NOTES,
	ALLOCATION_BEFORE_DELIVERY_NOTES,
)


# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]



class TestDeliveryLoad(UnitTestCase):
	"""
	Integration tests for DeliveryLoad.
	Use this class for testing interactions between multiple components.
	"""

	def test_capacity_fit_allows_unlimited_dimension(self):
		self.assertTrue(_fits(100, 0))

	def test_capacity_fit_rejects_over_capacity(self):
		self.assertFalse(_fits(101, 100))

	def test_calculate_load_totals(self):
		load = SimpleNamespace(
			delivery_orders=[
				SimpleNamespace(weight=100, volume=10, pallets=2, cases=20, crates=4, delivery_address="A"),
				SimpleNamespace(weight=50, volume=5, pallets=1, cases=10, crates=2, delivery_address="B"),
			],
		)
		calculate_load_totals(load)
		self.assertEqual(load.total_weight, 150)
		self.assertEqual(load.total_volume, 15)
		self.assertEqual(load.total_pallets, 3)
		self.assertEqual(load.total_stops, 2)

	@patch("vuna_transport.services.execution.execution_service._requires_vehicle_allocation", return_value=True)
	def test_after_notes_policy_creates_delivery_notes_before_allocation(self, _requires_allocation):
		result = _get_next_action(
			self._submitted_scheduled_load(),
			None,
			None,
			{"DO-1"},
			[],
			[],
			[],
			None,
			[],
			[],
			[],
			ALLOCATION_AFTER_DELIVERY_NOTES,
		)
		self.assertEqual(result["key"], "create_delivery_notes")

	@patch("vuna_transport.services.execution.execution_service._requires_vehicle_allocation", return_value=True)
	def test_before_notes_policy_allocates_vehicle_first(self, _requires_allocation):
		result = _get_next_action(
			self._submitted_scheduled_load(),
			None,
			None,
			{"DO-1"},
			[],
			[],
			[],
			None,
			[],
			[],
			[],
			ALLOCATION_BEFORE_DELIVERY_NOTES,
		)
		self.assertEqual(result["key"], "create_vehicle_allocation")

	@patch("vuna_transport.services.execution.execution_service._requires_vehicle_allocation", return_value=True)
	def test_after_notes_policy_allocates_after_draft_notes_exist(self, _requires_allocation):
		draft_note = SimpleNamespace(name="DN-1", docstatus=0)
		result = _get_next_action(
			self._submitted_scheduled_load(),
			None,
			None,
			{"DO-1"},
			[draft_note],
			[],
			[draft_note],
			None,
			[],
			[],
			[],
			ALLOCATION_AFTER_DELIVERY_NOTES,
		)
		self.assertEqual(result["key"], "create_vehicle_allocation")

	def test_open_delivery_exception_is_the_next_action(self):
		load = self._submitted_scheduled_load()
		load.status = "Exception Resolution"
		result = _get_next_action(
			load,
			None,
			None,
			{"DO-1"},
			[],
			[],
			[],
			None,
			[],
			[],
			[SimpleNamespace(name="DEX-1")],
			ALLOCATION_AFTER_DELIVERY_NOTES,
		)
		self.assertEqual(result["key"], "resolve_delivery_exceptions")

	@staticmethod
	def _submitted_scheduled_load():
		return SimpleNamespace(
			docstatus=1,
			status="Approved",
			planned_departure="2026-08-22 08:00:00",
			planned_arrival="2026-08-22 12:00:00",
		)

	def test_direct_delivery_note_uses_allocated_quantity(self):
		item = SimpleNamespace(
			so_detail="SO-ITEM-1",
			qty=10,
			stock_qty=10,
			conversion_factor=2,
			rate=100,
			base_rate=100,
			amount=1000,
			base_amount=1000,
		)
		delivery_note = SimpleNamespace(items=[item])
		apply_allocated_delivery_quantities(delivery_note, {"SO-ITEM-1": 4})
		self.assertEqual(item.qty, 4)
		self.assertEqual(item.stock_qty, 8)
		self.assertEqual(item.amount, 400)

	def test_partial_allocation_recalculates_every_capacity_dimension(self):
		delivery_order_item = SimpleNamespace(
			name="DOI-1",
			transport_qty=100,
			weight=1000,
			weight_uom="Kg",
			volume=20,
			volume_uom="Cubic Meter",
			pallets=10,
			cases=100,
			crates=50,
		)
		metrics = calculate_allocated_measurements(delivery_order_item, 50)
		self.assertEqual(metrics["weight"], 500)
		self.assertEqual(metrics["volume"], 10)
		self.assertEqual(metrics["pallets"], 5)
		self.assertEqual(metrics["cases"], 50)
		self.assertEqual(metrics["crates"], 25)

	def test_capacity_buffer_reduces_usable_capacity(self):
		self.assertEqual(apply_capacity_buffer(100, 5), 95)
		self.assertEqual(apply_capacity_buffer(0, 5), 0)

	def test_delivery_note_cannot_exceed_load_allocation(self):
		items = [SimpleNamespace(so_detail="SOI-1", item_code="ITEM-1", qty=51)]
		with self.assertRaises(ValidationError):
			validate_delivery_note_item_allocations(items, {"SOI-1": 50}, "LOAD-1")

	def test_delivery_trip_stops_must_match_submitted_delivery_notes(self):
		validate_delivery_trip_note_set({"DN-1", "DN-2"}, ["DN-1", "DN-2"])
		with self.assertRaises(ValidationError):
			validate_delivery_trip_note_set({"DN-1", "DN-2"}, ["DN-1"])

	def test_completed_load_cannot_return_to_planning(self):
		self.assertFalse(is_load_status_transition_allowed("Completed", "Approved"))
		self.assertTrue(is_load_status_transition_allowed("Completed", "Dispatched"))

	def test_vehicle_category_rejects_any_exceeded_capacity_dimension(self):
		category = SimpleNamespace(
			refrigerated=1,
			tail_lift=1,
			capacities={"weight": 8000, "volume": 20, "pallets": 12, "cases": 0, "crates": 0},
		)
		result = evaluate_vehicle_category(
			{"weight": 6000, "volume": 21, "pallets": 10, "cases": 0, "crates": 0},
			category,
		)
		self.assertFalse(result["compatible"])
		self.assertEqual(result["utilization"]["volume"], 105)

	def test_vehicle_category_enforces_body_and_special_equipment(self):
		category = SimpleNamespace(
			refrigerated=0,
			tail_lift=1,
			body_type="Rigid Truck",
			special_equipment="Crane, Temperature Logger",
			capacities={"weight": 8000, "volume": 30, "pallets": 0, "cases": 0, "crates": 0},
		)
		result = evaluate_vehicle_category(
			{"weight": 1000, "volume": 5, "pallets": 0, "cases": 0, "crates": 0},
			category,
			{
				"required_body_type": "Rigid Truck",
				"special_equipment_required": "Temperature Logger",
			},
		)
		self.assertTrue(result["compatible"])
		result = evaluate_vehicle_category(
			{"weight": 1000, "volume": 5, "pallets": 0, "cases": 0, "crates": 0},
			category,
			{"required_body_type": "Van"},
		)
		self.assertFalse(result["compatible"])

	def test_oversized_order_can_be_sized_as_a_partial_load(self):
		category = SimpleNamespace(
			capacities={"weight": 5000, "volume": 20, "pallets": 0, "cases": 0, "crates": 0}
		)
		fraction = maximum_fitting_fraction(
			{"weight": 10000, "volume": 25, "pallets": 0, "cases": 0, "crates": 0},
			category,
		)
		self.assertEqual(fraction, 0.5)

	def test_nearby_destinations_score_better_than_distant_destinations(self):
		nearby = [
			SimpleNamespace(destination_latitude=-1.2864, destination_longitude=36.8172),
			SimpleNamespace(destination_latitude=-1.2921, destination_longitude=36.8219),
		]
		distant = [
			nearby[0],
			SimpleNamespace(destination_latitude=-0.3031, destination_longitude=36.0800),
		]
		self.assertGreater(
			calculate_geographic_cohesion(nearby)["score"],
			calculate_geographic_cohesion(distant)["score"],
		)

	def test_missing_coordinates_reduce_geographic_confidence_without_failure(self):
		orders = [
			SimpleNamespace(destination_latitude=-1.2864, destination_longitude=36.8172),
			SimpleNamespace(destination_latitude=None, destination_longitude=None),
		]
		result = calculate_geographic_cohesion(orders)
		self.assertEqual(result["missing_coordinate_count"], 1)
		self.assertLess(result["score"], 100)

	def test_overlapping_delivery_windows_score_better(self):
		overlap = [
			SimpleNamespace(delivery_from_time="08:00:00", delivery_to_time="10:00:00"),
			SimpleNamespace(delivery_from_time="09:00:00", delivery_to_time="11:00:00"),
		]
		separate = [
			overlap[0],
			SimpleNamespace(delivery_from_time="16:00:00", delivery_to_time="18:00:00"),
		]
		self.assertGreater(
			calculate_delivery_window_compatibility(overlap)["score"],
			calculate_delivery_window_compatibility(separate)["score"],
		)
