# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import UnitTestCase


class TestTransportCostComponent(UnitTestCase):
	def test_component_doctype_is_a_master(self):
		self.assertFalse(frappe.get_meta("Transport Cost Component").istable)
