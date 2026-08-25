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
			vuna_ownership_type="Company Owned",
			vuna_asset="ASSET-1",
		)
		self.asset = SimpleNamespace(
			name="ASSET-1",
			company="Vuna Foods",
			status="Submitted",
			docstatus=1,
		)
		self.compliance = SimpleNamespace(
			compliant=True,
			status="Compliant",
			reasons=[],
			warnings=[],
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

	def test_company_owned_transport_vehicle_requires_asset(self):
		self.vehicle.vuna_asset = None
		result = self._evaluate(asset=None)
		self.assertFalse(result.dispatch_eligible)
		self.assertEqual(result.operational_state, "Unavailable")
		self.assertIn("ASSET_REQUIRED", [reason.code for reason in result.reasons])

	def test_subcontracted_vehicle_does_not_require_asset(self):
		self.vehicle.vuna_ownership_type = "Subcontracted"
		self.vehicle.vuna_asset = None
		result = self._evaluate(asset=None)
		self.assertTrue(result.dispatch_eligible)

	def test_asset_in_maintenance_blocks_dispatch(self):
		self.asset.status = "In Maintenance"
		result = self._evaluate()
		self.assertFalse(result.dispatch_eligible)
		self.assertEqual(result.operational_state, "Under Maintenance")
		self.assertIn("ASSET_IN_MAINTENANCE", [reason.code for reason in result.reasons])

	def test_pending_asset_repair_is_explainable(self):
		repair = SimpleNamespace(name="AR-1", asset="ASSET-1")
		result = self._evaluate(asset_repairs=[repair])
		self.assertFalse(result.dispatch_eligible)
		self.assertEqual(result.operational_state, "Under Maintenance")
		self.assertEqual(result.active_asset_repair, "AR-1")
		self.assertIn("AR-1", result.primary_reason)

	def test_overlapping_availability_block_is_explainable(self):
		block = SimpleNamespace(
			name="VAB-1",
			reason="Operational Assignment",
			to_datetime="2026-08-24 11:00:00",
		)
		result = self._evaluate(availability_blocks=[block])
		self.assertFalse(result.dispatch_eligible)
		self.assertEqual(result.operational_state, "Unavailable")
		self.assertEqual(result.current_availability_block, "VAB-1")
		self.assertEqual(result.reasons[0].code, "AVAILABILITY_BLOCK")

	def test_compliance_block_is_included_in_availability(self):
		reason = SimpleNamespace(
			code="DOCUMENT_MISSING",
			message="Mandatory Inspection Certificate is missing.",
			reference_doctype=None,
			reference_name=None,
		)
		compliance = SimpleNamespace(
			compliant=False,
			status="Blocked",
			reasons=[reason],
			warnings=[],
		)
		result = self._evaluate(compliance=compliance)
		self.assertFalse(result.dispatch_eligible)
		self.assertEqual(result.operational_state, "Unavailable")
		self.assertEqual(result.compliance_status, "Blocked")

	def test_non_operational_asset_blocks_dispatch(self):
		self.asset.status = "Sold"
		result = self._evaluate()
		self.assertFalse(result.dispatch_eligible)
		self.assertEqual(result.operational_state, "Unavailable")
		self.assertIn("ASSET_NOT_OPERATIONAL", [reason.code for reason in result.reasons])

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

	def test_active_trip_keeps_maintenance_reason(self):
		self.asset.status = "Out of Order"
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
		self.assertEqual(result.operational_state, "In Trip")
		self.assertEqual(
			{"ASSET_OUT_OF_ORDER", "ACTIVE_TRIP"},
			{reason.code for reason in result.reasons},
		)

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

	def _evaluate(
		self,
		allocations=None,
		trips=None,
		loads=None,
		asset="default",
		asset_repairs=None,
		compliance="default",
		availability_blocks=None,
	):
		return _evaluate_vehicle(
			self.vehicle,
			self.asset if asset == "default" else asset,
			asset_repairs or [],
			self.compliance if compliance == "default" else compliance,
			availability_blocks or [],
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
