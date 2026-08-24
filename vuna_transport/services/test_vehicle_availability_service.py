# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from types import SimpleNamespace

from frappe.exceptions import ValidationError
from frappe.tests import UnitTestCase

from vuna_transport.services.driver_availability_service import (
	_evaluate_driver,
	_normalize_interval as normalize_driver_interval,
)
from vuna_transport.services.resource_scheduling_service import interval_overlaps
from vuna_transport.services.vehicle_availability_service import (
	_evaluate_vehicle,
	_normalize_interval as normalize_vehicle_interval,
)


class TestVehicleAvailabilityService(UnitTestCase):
	def setUp(self):
		self.vehicle = SimpleNamespace(
			name="KDA 123A",
			license_plate="KDA 123A",
			company="Vuna Foods",
			vuna_vehicle_category="8 Ton Truck",
			vuna_transport_enabled=1,
		)
		self.from_time = "2026-08-24 08:00:00"
		self.to_time = "2026-08-24 12:00:00"

	def test_healthy_unallocated_vehicle_is_available(self):
		result = self._evaluate()
		self.assertTrue(result.dispatch_eligible)
		self.assertEqual(result.operational_state, "Available")
		self.assertFalse(result.reasons)

	def test_transport_disabled_vehicle_is_unavailable(self):
		self.vehicle.vuna_transport_enabled = 0
		result = self._evaluate()
		self.assertFalse(result.dispatch_eligible)
		self.assertEqual(result.operational_state, "Unavailable")
		self.assertEqual(result.reasons[0].code, "TRANSPORT_DISABLED")

	def test_overlapping_allocation_uses_delivery_load_state(self):
		allocation = SimpleNamespace(
			name="VA-1",
			delivery_load="DL-1",
			effective_from=None,
			effective_to=None,
		)
		loads = {
			"DL-1": SimpleNamespace(
				planned_departure="2026-08-24 09:00:00",
				planned_arrival="2026-08-24 11:00:00",
				status="Loading",
			)
		}
		result = self._evaluate(allocations=[allocation], loads=loads)
		self.assertFalse(result.dispatch_eligible)
		self.assertEqual(result.operational_state, "Loading")
		self.assertEqual(result.current_allocation, "VA-1")

	def test_non_overlapping_allocation_does_not_block_vehicle(self):
		allocation = SimpleNamespace(
			name="VA-1",
			delivery_load="DL-1",
			effective_from=None,
			effective_to=None,
		)
		loads = {
			"DL-1": SimpleNamespace(
				planned_departure="2026-08-24 12:00:00",
				planned_arrival="2026-08-24 14:00:00",
				status="Approved",
			)
		}
		self.assertTrue(
			self._evaluate(allocations=[allocation], loads=loads).dispatch_eligible
		)

	def test_active_trip_has_in_trip_state(self):
		trip = SimpleNamespace(
			name="DT-1",
			vuna_delivery_load="DL-1",
			departure_time="2026-08-24 08:00:00",
			status="In Transit",
		)
		loads = {
			"DL-1": SimpleNamespace(
				planned_departure="2026-08-24 08:00:00",
				planned_arrival="2026-08-24 12:00:00",
				status="Dispatched",
			)
		}
		result = self._evaluate(trips=[trip], loads=loads)
		self.assertFalse(result.dispatch_eligible)
		self.assertEqual(result.operational_state, "In Trip")
		self.assertEqual(result.current_trip, "DT-1")
		self.assertIn("DT-1", result.primary_reason)

	def test_adjacent_windows_do_not_overlap(self):
		self.assertFalse(
			interval_overlaps(
				"2026-08-24 08:00:00",
				"2026-08-24 10:00:00",
				"2026-08-24 10:00:00",
				"2026-08-24 12:00:00",
			)
		)

	def test_invalid_vehicle_window_has_validation_message(self):
		with self.assertRaises(ValidationError):
			normalize_vehicle_interval("undefined 00:00:00", "2026-08-25 00:00:00")

	def _evaluate(self, allocations=None, trips=None, loads=None):
		return _evaluate_vehicle(
			self.vehicle,
			allocations or [],
			trips or [],
			loads or {},
			self.from_time,
			self.to_time,
			"Vuna Foods",
			None,
		)


class TestDriverAvailabilityService(UnitTestCase):
	def setUp(self):
		self.driver = SimpleNamespace(
			name="DRIVER-1",
			full_name="Test Driver",
			status="Active",
			expiry_date="2027-01-01",
		)
		self.from_time = "2026-08-24 08:00:00"
		self.to_time = "2026-08-24 12:00:00"

	def test_active_licensed_driver_is_available(self):
		result = self._evaluate()
		self.assertTrue(result.dispatch_eligible)
		self.assertEqual(result.operational_state, "Available")

	def test_inactive_driver_is_unavailable(self):
		self.driver.status = "Suspended"
		result = self._evaluate()
		self.assertFalse(result.dispatch_eligible)
		self.assertEqual(result.reasons[0].code, "DRIVER_INACTIVE")

	def test_expired_licence_is_unavailable(self):
		self.driver.expiry_date = "2026-08-23"
		result = self._evaluate()
		self.assertFalse(result.dispatch_eligible)
		self.assertEqual(result.reasons[0].code, "LICENCE_EXPIRED")

	def test_overlapping_allocation_blocks_driver(self):
		allocation = SimpleNamespace(
			name="VA-1",
			delivery_load="DL-1",
			effective_from=None,
			effective_to=None,
		)
		loads = {
			"DL-1": SimpleNamespace(
				planned_departure="2026-08-24 09:00:00",
				planned_arrival="2026-08-24 11:00:00",
			)
		}
		result = self._evaluate(allocations=[allocation], loads=loads)
		self.assertFalse(result.dispatch_eligible)
		self.assertEqual(result.operational_state, "Allocated")

	def test_active_trip_blocks_driver(self):
		trip = SimpleNamespace(
			name="DT-1",
			vuna_delivery_load="DL-1",
			departure_time="2026-08-24 08:00:00",
			status="In Transit",
		)
		loads = {
			"DL-1": SimpleNamespace(
				planned_departure="2026-08-24 08:00:00",
				planned_arrival="2026-08-24 12:00:00",
			)
		}
		result = self._evaluate(trips=[trip], loads=loads)
		self.assertFalse(result.dispatch_eligible)
		self.assertEqual(result.operational_state, "In Trip")
		self.assertEqual(result.current_trip, "DT-1")

	def test_invalid_driver_window_has_validation_message(self):
		with self.assertRaises(ValidationError):
			normalize_driver_interval("undefined 00:00:00", "2026-08-25 00:00:00")

	def _evaluate(self, allocations=None, trips=None, loads=None):
		return _evaluate_driver(
			self.driver,
			allocations or [],
			trips or [],
			loads or {},
			self.from_time,
			self.to_time,
		)
