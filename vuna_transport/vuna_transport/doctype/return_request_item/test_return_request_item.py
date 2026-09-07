# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from frappe.tests import UnitTestCase


class TestReturnRequestItem(UnitTestCase):
	def test_doctype_is_a_child_table(self):
		from frappe import get_meta

		self.assertTrue(get_meta("Return Request Item").istable)
