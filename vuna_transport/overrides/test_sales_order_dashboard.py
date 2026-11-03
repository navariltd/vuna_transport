# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from frappe.tests import UnitTestCase

from vuna_transport.overrides.sales_order_dashboard import get_data


class TestSalesOrderDashboard(UnitTestCase):
	def test_transport_fulfillment_links_are_added(self):
		data = get_data({"non_standard_fieldnames": {}, "transactions": []})

		self.assertEqual(data["non_standard_fieldnames"]["Delivery Order"], "sales_order")
		self.assertEqual(data["non_standard_fieldnames"]["Transport Load"], "sales_order")
		self.assertEqual(data["transactions"][0]["items"], ["Delivery Order", "Transport Load"])
