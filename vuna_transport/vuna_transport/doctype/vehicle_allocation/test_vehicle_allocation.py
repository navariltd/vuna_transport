# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

from frappe.exceptions import ValidationError
from frappe.tests import UnitTestCase

from vuna_transport.services.fleet.vehicle_allocation_service import (
	_get_replacement_stage,
	_validate_initial_allocation_sequence,
	_validate_planning_window,
	time_windows_overlap,
)


class TestVehicleAllocation(UnitTestCase):
	def test_overlapping_time_windows(self):
		first = SimpleNamespace(planned_departure="2026-08-15 08:00:00", planned_arrival="2026-08-15 12:00:00")
		second = SimpleNamespace(planned_departure="2026-08-15 11:00:00", planned_arrival="2026-08-15 14:00:00")
		self.assertTrue(time_windows_overlap(first, second))

	def test_non_overlapping_time_windows(self):
		first = SimpleNamespace(planned_departure="2026-08-15 08:00:00", planned_arrival="2026-08-15 10:00:00")
		second = SimpleNamespace(planned_departure="2026-08-15 10:00:00", planned_arrival="2026-08-15 14:00:00")
		self.assertFalse(time_windows_overlap(first, second))

	def test_vehicle_allocation_requires_a_complete_planning_window(self):
		load = SimpleNamespace(name="LOAD-1", planned_departure=None, planned_arrival=None)
		with self.assertRaises(ValidationError):
			_validate_planning_window(load)

	def test_vehicle_allocation_rejects_invalid_planning_window(self):
		load = SimpleNamespace(
			name="LOAD-1",
			planned_departure="2026-08-16 12:00:00",
			planned_arrival="2026-08-16 10:00:00",
		)
		with self.assertRaises(ValidationError):
			_validate_planning_window(load)

	@patch(
		"vuna_transport.services.fleet.vehicle_allocation_service.allocation_is_required_before_delivery_notes",
		return_value=False,
	)
	@patch("vuna_transport.services.fleet.vehicle_allocation_service.frappe.get_all", return_value=[])
	def test_after_notes_policy_rejects_initial_allocation_without_notes(self, _get_all, _policy):
		load = SimpleNamespace(
			name="LOAD-1",
			active_vehicle_allocation=None,
			delivery_method=None,
			delivery_orders=[SimpleNamespace(delivery_order="DO-1")],
		)
		with self.assertRaises(ValidationError):
			_validate_initial_allocation_sequence(load)

	def test_replacement_stage_tracks_actual_trip_execution(self):
		self.assertEqual(
			_get_replacement_stage(SimpleNamespace(status="Scheduled")),
			"Before Departure",
		)
		self.assertEqual(
			_get_replacement_stage(SimpleNamespace(status="In Transit")),
			"In Transit",
		)
