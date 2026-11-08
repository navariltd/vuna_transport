# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# See license.txt

from frappe.tests import UnitTestCase

from vuna_transport.vuna_transport.doctype.return_request.return_request_dashboard import get_data


class TestReturnRequestDashboard(UnitTestCase):
	def test_generated_return_documents_are_available_as_connections(self):
		data = get_data()

		self.assertEqual(data["fieldname"], "vuna_return_request")
		self.assertEqual(data["internal_links"]["Delivery Note"], "return_stock_document")
		self.assertEqual(data["internal_links"]["Quality Inspection"], ["items", "quality_inspection"])
		self.assertEqual(data["internal_links"]["Sales Invoice"], "credit_note")
		self.assertEqual(data["internal_links"]["Sales Order"], "replacement_sales_order")
		self.assertEqual(data["internal_links"]["Payment Entry"], "refund_payment_entry")
		self.assertNotIn("Delivery Order", data["internal_links"])
		self.assertEqual(data["non_standard_fieldnames"]["Transport Demand"], "source_document")
