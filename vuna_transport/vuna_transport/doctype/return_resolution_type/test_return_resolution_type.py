# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from frappe.tests import UnitTestCase


class TestReturnResolutionType(UnitTestCase):
	def test_resolution_type_has_company_scope(self):
		from frappe import get_meta

		self.assertIsNotNone(get_meta("Return Resolution Type").get_field("company"))
