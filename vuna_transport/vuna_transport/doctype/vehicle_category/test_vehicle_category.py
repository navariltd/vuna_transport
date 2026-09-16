# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

from frappe.exceptions import ValidationError
from frappe.tests import IntegrationTestCase, UnitTestCase

from vuna_transport.vuna_transport.doctype.vehicle_category.vehicle_category import VehicleCategory
from vuna_transport.services.fleet.vehicle_category_service import validate_minimum_load_requirements

# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]


class IntegrationTestVehicleCategory(IntegrationTestCase):
	"""
	Integration tests for VehicleCategory.
	Use this class for testing interactions between multiple components.
	"""

	pass


class TestVehicleCategoryValidation(UnitTestCase):
	@patch("vuna_transport.vuna_transport.doctype.vehicle_category.vehicle_category.frappe.get_cached_doc")
	def test_override_requires_transport_setting(self, get_cached_doc):
		get_cached_doc.return_value = SimpleNamespace(
			allow_capacity_override=0,
			default_weight_uom="Kg",
			default_volume_uom="Cubic Meter",
		)
		category = VehicleCategory(
			{
				"doctype": "Vehicle Category",
				"capacity_override_percentage": 5,
				"max_payload_weight_uom": "Kg",
				"max_volume_uom": "Cubic Meter",
			}
		)

		with self.assertRaises(ValidationError):
			category.validate()

	@patch("vuna_transport.vuna_transport.doctype.vehicle_category.vehicle_category.frappe.get_cached_doc")
	def test_minimum_payload_cannot_exceed_maximum(self, get_cached_doc):
		get_cached_doc.return_value = SimpleNamespace(
			allow_capacity_override=0,
			default_weight_uom="Kg",
			default_volume_uom="Cubic Meter",
		)
		category = VehicleCategory(
			{
				"doctype": "Vehicle Category",
				"max_payload_weight": 9000,
				"max_payload_weight_uom": "Kg",
				"minimum_payload_weight": 9500,
				"minimum_payload_weight_uom": "Kg",
				"max_volume": 10,
				"max_volume_uom": "Cubic Meter",
			}
		)

		with self.assertRaises(ValidationError):
			category.validate()

	def test_minimum_payload_is_checked_at_vehicle_allocation(self):
		category = SimpleNamespace(
			name="8 Ton Truck",
			max_payload_weight=9000,
			max_payload_weight_uom="Kg",
			minimum_payload_weight=6000,
			minimum_payload_weight_uom="Kg",
			max_volume=20,
			max_volume_uom="Cubic Meter",
			minimum_volume=0,
			minimum_volume_uom="Cubic Meter",
		)
		load = SimpleNamespace(total_weight=5000, total_volume=10, weight_uom="Kg", volume_uom="Cubic Meter")

		with self.assertRaises(ValidationError):
			validate_minimum_load_requirements(load, category)

		load.total_weight = 6000
		validate_minimum_load_requirements(load, category)
