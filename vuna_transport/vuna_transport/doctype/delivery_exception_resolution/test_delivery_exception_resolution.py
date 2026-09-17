# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

import frappe
from frappe.tests import UnitTestCase


class TestDeliveryExceptionResolution(UnitTestCase):
	def test_resolution_is_a_child_table(self):
		self.assertTrue(frappe.get_meta("Delivery Exception Resolution").istable)
