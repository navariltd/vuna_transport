# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import UnitTestCase


class TestTransportCostStructure(UnitTestCase):
	def test_model_controller_exists(self):
		self.assertFalse(frappe.get_meta("Transport Cost Structure").istable)

	def test_cost_structure_is_submittable(self):
		self.assertTrue(frappe.get_meta("Transport Cost Structure").is_submittable)
