# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import UnitTestCase


class TestDeliveryGroup(UnitTestCase):
	def test_delivery_group_is_a_master_doctype(self):
		self.assertFalse(frappe.get_meta("Delivery Group").istable)
