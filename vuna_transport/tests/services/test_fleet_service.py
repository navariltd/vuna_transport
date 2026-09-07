# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.exceptions import ValidationError
from frappe.tests import UnitTestCase

from vuna_transport.services.fleet.fleet_service import (
	_validate_vehicle_asset,
	_validate_vehicle_operational_roles,
	get_vehicles_by_operational_role,
	sync_vehicle_operational_snapshots,
	validate_vehicle,
)


class TestVehicleAssetValidation(UnitTestCase):
	def setUp(self):
		self.vehicle = SimpleNamespace(
			name="KDA 123A",
			company="Vuna Foods",
			vuna_asset="ASSET-1",
		)
		self.asset = SimpleNamespace(
			name="ASSET-1",
			company="Vuna Foods",
		)

	@patch("vuna_transport.services.fleet.fleet_service.frappe.db.get_value")
	@patch("vuna_transport.services.fleet.fleet_service.frappe.get_cached_doc")
	def test_matching_asset_can_be_linked(self, get_cached_doc, get_value):
		get_cached_doc.return_value = self.asset
		get_value.return_value = None

		_validate_vehicle_asset(self.vehicle)

		get_value.assert_called_once()

	@patch("vuna_transport.services.fleet.fleet_service.frappe.get_cached_doc")
	def test_asset_company_mismatch_is_rejected(self, get_cached_doc):
		self.asset.company = "Other Company"
		get_cached_doc.return_value = self.asset

		with self.assertRaises(ValidationError):
			_validate_vehicle_asset(self.vehicle)

	@patch("vuna_transport.services.fleet.fleet_service.frappe.db.get_value")
	@patch("vuna_transport.services.fleet.fleet_service.frappe.get_cached_doc")
	def test_asset_sets_missing_vehicle_company(self, get_cached_doc, get_value):
		self.vehicle.company = None
		get_cached_doc.return_value = self.asset
		get_value.return_value = None

		_validate_vehicle_asset(self.vehicle)

		self.assertEqual(self.vehicle.company, "Vuna Foods")

	@patch("vuna_transport.services.fleet.fleet_service.frappe.db.get_value")
	@patch("vuna_transport.services.fleet.fleet_service.frappe.get_cached_doc")
	def test_asset_cannot_link_to_multiple_vehicles(self, get_cached_doc, get_value):
		get_cached_doc.return_value = self.asset
		get_value.return_value = "KDB 456B"

		with self.assertRaises(ValidationError):
			_validate_vehicle_asset(self.vehicle)

	def test_company_owned_transport_vehicle_requires_an_asset(self):
		self.vehicle.vuna_asset = None
		self.vehicle.vuna_transport_enabled = 1
		self.vehicle.vuna_vehicle_owner = "Company"
		with self.assertRaises(ValidationError):
			_validate_vehicle_asset(self.vehicle)

	@patch("vuna_transport.services.fleet.fleet_service.frappe.get_cached_doc")
	def test_model_without_category_does_not_clear_vehicle_category(self, get_cached_doc):
		vehicle = SimpleNamespace(
			vuna_vehicle_model="Isuzu Hatchback",
			vuna_vehicle_category="Refrigerated Van",
		)
		get_cached_doc.return_value = SimpleNamespace(
			disabled=0,
			manufacturer="Isuzu",
			model_name="Hatchback",
			vehicle_category=None,
			fuel_type=None,
		)

		validate_vehicle(vehicle)

		self.assertEqual(vehicle.vuna_vehicle_category, "Refrigerated Van")


class TestVehicleOperationalRoles(UnitTestCase):
	def test_duplicate_roles_are_rejected(self):
		vehicle = frappe._dict(
			vuna_operational_roles=[
				frappe._dict(operational_role="Distribution"),
				frappe._dict(operational_role="Distribution"),
			]
		)

		with self.assertRaises(ValidationError):
			_validate_vehicle_operational_roles(vehicle)

	@patch("vuna_transport.services.fleet.fleet_service.frappe.get_all")
	def test_disabled_roles_are_rejected(self, get_all):
		get_all.return_value = ["Distribution"]
		vehicle = frappe._dict(vuna_operational_roles=[frappe._dict(operational_role="Distribution")])

		with self.assertRaises(ValidationError):
			_validate_vehicle_operational_roles(vehicle)

	def test_vehicles_can_be_queried_by_role_and_company(self):
		with patch(
			"vuna_transport.services.fleet.fleet_service.frappe.get_all",
			side_effect=[["KDA 123A", "KDB 456B"], ["KDA 123A"]],
		) as get_all:
			result = get_vehicles_by_operational_role("Distribution", company="Yovuna Tech")

		self.assertEqual(result, ["KDA 123A"])
		self.assertEqual(get_all.call_count, 2)


class TestVehicleStatusSnapshot(UnitTestCase):
	@patch("vuna_transport.services.fleet.fleet_service.get_vehicle_availability_map")
	@patch("vuna_transport.services.fleet.fleet_service.frappe.db.set_value")
	@patch("vuna_transport.services.fleet.fleet_service.frappe.get_meta")
	@patch("vuna_transport.services.fleet.fleet_service.frappe.db.table_exists")
	def test_snapshot_uses_canonical_availability(self, table_exists, get_meta, set_value, get_availability):
		table_exists.return_value = True
		get_meta.return_value.has_field.return_value = True
		get_availability.return_value = {
			"KDA 123A": frappe._dict(
				operational_state="Under Maintenance",
				dispatch_eligible=False,
			)
		}

		sync_vehicle_operational_snapshots(["KDA 123A"])

		values = set_value.call_args.args[2]
		self.assertEqual(values["vuna_operational_status"], "Under Maintenance")
		self.assertEqual(values["vuna_dispatch_eligible"], 0)
