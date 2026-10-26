from frappe.tests import UnitTestCase

from vuna_transport.overrides.delivery_note_dashboard import get_data


class TestDeliveryNoteDashboard(UnitTestCase):
	def test_return_request_is_added_to_returns_connections(self):
		data = {
			"non_standard_fieldnames": {},
			"transactions": [{"label": "Returns", "items": ["Stock Entry"]}],
		}

		result = get_data(data)

		self.assertEqual(result["non_standard_fieldnames"]["Return Request"], "return_stock_document")
		self.assertEqual(result["transactions"][0]["items"], ["Stock Entry", "Return Request"])
