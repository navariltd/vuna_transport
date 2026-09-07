# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

from frappe.exceptions import ValidationError
from frappe.tests import IntegrationTestCase, UnitTestCase

from vuna_transport.vuna_transport.doctype.vehicle_category.vehicle_category import VehicleCategory

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
