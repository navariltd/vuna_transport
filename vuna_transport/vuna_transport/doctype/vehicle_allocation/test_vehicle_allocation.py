# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from frappe.exceptions import ValidationError
from frappe.tests import UnitTestCase

from vuna_transport.services.fleet.vehicle_allocation_service import (
	_get_replacement_stage,
	_validate_initial_allocation_sequence,
	_validate_planning_window,
	_validate_subcontractor,
	get_load_resource_window,
	prepare_allocation_cancellation,
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

	def test_vehicle_allocation_allows_date_only_planning(self):
		load = SimpleNamespace(
			name="LOAD-1",
			planned_departure=None,
			planned_arrival=None,
			dispatch_date="2026-08-24",
		)
		_validate_planning_window(load)
		from_time, to_time = get_load_resource_window(load)
		self.assertLess(from_time, to_time)

	def test_vehicle_allocation_rejects_partial_planning_window(self):
		load = SimpleNamespace(
			name="LOAD-1",
			planned_departure="2026-08-24 08:00:00",
			planned_arrival=None,
			dispatch_date="2026-08-24",
		)
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

	def test_subcontractor_requires_a_physical_vehicle_record(self):
		allocation = SimpleNamespace(vehicle=None)
		load = SimpleNamespace(name="LOAD-1")
		with self.assertRaises(ValidationError):
			_validate_subcontractor(allocation, load)

	@patch("vuna_transport.services.fleet.vehicle_allocation_service._validate_vehicle_is_available")
	@patch("vuna_transport.services.fleet.vehicle_allocation_service._validate_vehicle_category_for_load")
	@patch("vuna_transport.services.fleet.vehicle_allocation_service.lock_document")
	@patch("vuna_transport.services.fleet.vehicle_allocation_service.frappe.get_doc")
	def test_subcontractor_vehicle_populates_category_and_supplier_server_side(
		self, get_doc, lock_document, validate_category, validate_vehicle
	):
		allocation = SimpleNamespace(
			vehicle="KDA 123A",
			subcontractor_vehicle_category="Stale Category",
			supplier=None,
			driver=None,
		)
		load = SimpleNamespace(name="LOAD-1")
		get_doc.return_value = SimpleNamespace(
			vuna_vehicle_category="8 Ton Truck",
			vuna_supplier="ABC Logistics",
		)

		_validate_subcontractor(allocation, load)

		self.assertEqual(allocation.subcontractor_vehicle_category, "8 Ton Truck")
		self.assertEqual(allocation.supplier, "ABC Logistics")

	@patch("vuna_transport.services.fleet.vehicle_allocation_service.frappe.get_doc")
	def test_cancellation_detaches_active_allocation_from_load(self, get_doc):
		load = SimpleNamespace(active_vehicle_allocation="VA-1", db_set=MagicMock())
		get_doc.return_value = load
		prepare_allocation_cancellation(SimpleNamespace(name="VA-1", delivery_load="LOAD-1"))
		load.db_set.assert_called_once_with("active_vehicle_allocation", None, update_modified=True)
