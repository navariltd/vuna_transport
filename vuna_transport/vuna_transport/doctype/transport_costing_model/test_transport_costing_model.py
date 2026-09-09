# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import UnitTestCase


class TestTransportCostingModel(UnitTestCase):
	def test_model_controller_exists(self):
		self.assertFalse(frappe.get_meta("Transport Costing Model").istable)
