# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from frappe.exceptions import ValidationError
from frappe.tests import IntegrationTestCase, UnitTestCase

from vuna_transport.vuna_transport.doctype.vehicle_model.vehicle_model import validate_model_year

EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


class IntegrationTestVehicleModel(IntegrationTestCase):
	pass


class TestVehicleModel(UnitTestCase):
	def test_unknown_year_is_allowed(self):
		validate_model_year(None)
		validate_model_year("")

	def test_zero_year_is_rejected(self):
		with self.assertRaises(ValidationError):
			validate_model_year(0)

	def test_future_year_beyond_next_year_is_rejected(self):
		from frappe.utils import getdate, nowdate

		with self.assertRaises(ValidationError):
			validate_model_year(getdate(nowdate()).year + 2)
