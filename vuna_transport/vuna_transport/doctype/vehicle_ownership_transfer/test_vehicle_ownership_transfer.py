# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import Mock, patch

from frappe.exceptions import ValidationError
from frappe.tests import UnitTestCase

from vuna_transport.vuna_transport.doctype.vehicle_ownership_transfer.vehicle_ownership_transfer import (
	VehicleOwnershipTransfer,
)


class TestVehicleOwnershipTransfer(UnitTestCase):
	@patch(
		"vuna_transport.vuna_transport.doctype.vehicle_ownership_transfer.vehicle_ownership_transfer.today",
		return_value="2026-10-27",
	)
	def test_effective_date_cannot_be_backdated(self, _today):
		transfer = SimpleNamespace(effective_date="2026-10-26")

		with self.assertRaises(ValidationError):
			VehicleOwnershipTransfer._validate_effective_date(transfer)

	@patch(
		"vuna_transport.vuna_transport.doctype.vehicle_ownership_transfer.vehicle_ownership_transfer.today",
		return_value="2026-10-27",
	)
	def test_effective_date_can_be_today(self, _today):
		transfer = SimpleNamespace(effective_date="2026-10-27")

		VehicleOwnershipTransfer._validate_effective_date(transfer)

	@patch("vuna_transport.services.fleet.fleet_service.sync_vehicle_operational_snapshots")
	@patch(
		"vuna_transport.vuna_transport.doctype.vehicle_ownership_transfer.vehicle_ownership_transfer.frappe.clear_document_cache"
	)
	@patch(
		"vuna_transport.vuna_transport.doctype.vehicle_ownership_transfer.vehicle_ownership_transfer.frappe.db.set_value"
	)
	def test_company_transfer_clears_vehicle_supplier(self, set_value, clear_cache, sync_snapshots):
		transfer = SimpleNamespace(
			vehicle="VEH-1",
			new_owner="Company",
			new_supplier=None,
			db_set=Mock(),
		)

		VehicleOwnershipTransfer.on_submit(transfer)

		set_value.assert_called_once_with(
			"Vehicle",
			"VEH-1",
			{"vuna_vehicle_owner": "Company", "vuna_supplier": None},
			update_modified=True,
		)
		clear_cache.assert_called_once_with("Vehicle", "VEH-1")
		sync_snapshots.assert_called_once_with(["VEH-1"])
