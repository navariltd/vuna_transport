# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from types import SimpleNamespace

from frappe.exceptions import ValidationError
from frappe.tests import UnitTestCase

from vuna_transport.services.fleet.vehicle_incident_service import (
	_validate_asset_repair_context,
	_validate_incident_context,
	_validate_replacement_context,
	_validate_resolution,
	validate_incident_submission,
)


class TestVehicleIncident(UnitTestCase):
	def setUp(self):
		self.incident = SimpleNamespace(
			name="VI-2026-00001",
			company="Vuna Foods",
			vehicle="KDA 123A",
			driver="DRIVER-1",
			delivery_load="DL-1",
			delivery_trip="DT-1",
			vehicle_allocation="VA-1",
			resolution_status="Open",
			vehicle_immobilized=1,
			resolution=None,
			resolved_on=None,
		)
		self.vehicle = SimpleNamespace(name="KDA 123A", company="Vuna Foods", vuna_asset="ASSET-1")
		self.load = SimpleNamespace(name="DL-1", company="Vuna Foods")
		self.trip = SimpleNamespace(
			name="DT-1",
			company="Vuna Foods",
			delivery_stops=[SimpleNamespace(vuna_delivery_load="DL-1")],
			vuna_vehicle_allocation="VA-1",
			vuna_current_vehicle_allocation="VA-1",
			vuna_current_vehicle="KDA 123A",
			vuna_current_driver="DRIVER-1",
			vehicle="KDA 123A",
			driver="DRIVER-1",
		)
		self.allocation = SimpleNamespace(
			name="VA-1",
			company="Vuna Foods",
			delivery_load="DL-1",
			vehicle="KDA 123A",
			driver="DRIVER-1",
		)

	def test_valid_incident_context(self):
		_validate_incident_context(
			self.incident,
			self.vehicle,
			load=self.load,
			trip=self.trip,
			allocation=self.allocation,
		)

	def test_cross_load_trip_is_rejected(self):
		self.trip.delivery_stops = [SimpleNamespace(vuna_delivery_load="DL-OTHER")]
		with self.assertRaises(ValidationError):
			_validate_incident_context(
				self.incident,
				self.vehicle,
				load=self.load,
				trip=self.trip,
				allocation=self.allocation,
			)

	def test_cross_vehicle_allocation_is_rejected(self):
		self.allocation.vehicle = "KDB 456B"
		with self.assertRaises(ValidationError):
			_validate_incident_context(
				self.incident,
				self.vehicle,
				load=self.load,
				allocation=self.allocation,
			)

	def test_allocation_without_physical_vehicle_is_rejected(self):
		self.allocation.vehicle = None
		with self.assertRaises(ValidationError):
			_validate_incident_context(
				self.incident,
				self.vehicle,
				load=self.load,
				allocation=self.allocation,
			)

	def test_resolved_incident_requires_resolved_on(self):
		self.incident.resolution_status = "Resolved"
		with self.assertRaises(ValidationError):
			_validate_resolution(self.incident)
		self.incident.resolved_on = "2026-08-25 10:00:00"
		_validate_resolution(self.incident)

	def test_immobilizing_incident_must_be_resolved_before_submission(self):
		with self.assertRaises(ValidationError):
			validate_incident_submission(self.incident)
		self.incident.resolution_status = "In Resolution"
		with self.assertRaises(ValidationError):
			validate_incident_submission(self.incident)
		self.incident.resolution_status = "Resolved"
		validate_incident_submission(self.incident)

	def test_non_immobilizing_incident_also_requires_resolution_before_submission(self):
		self.incident.vehicle_immobilized = 0
		with self.assertRaises(ValidationError):
			validate_incident_submission(self.incident)

	def test_replacement_allocation_linkage(self):
		replacement = SimpleNamespace(
			name="VA-2",
			delivery_load="DL-1",
			assignment_event="Reallocation",
			replacement_of="VA-1",
			vehicle_incident="VI-2026-00001",
		)
		_validate_replacement_context(self.incident, replacement)
		replacement.delivery_load = "DL-OTHER"
		with self.assertRaises(ValidationError):
			_validate_replacement_context(self.incident, replacement)

	def test_asset_repair_linkage(self):
		_validate_asset_repair_context(
			self.incident,
			self.vehicle,
			SimpleNamespace(name="AR-1", asset="ASSET-1"),
		)
		with self.assertRaises(ValidationError):
			_validate_asset_repair_context(
				self.incident,
				self.vehicle,
				SimpleNamespace(name="AR-2", asset="ASSET-2"),
			)
