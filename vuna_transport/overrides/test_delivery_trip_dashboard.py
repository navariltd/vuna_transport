from frappe.tests import UnitTestCase

from vuna_transport.overrides.delivery_trip_dashboard import get_data


class TestDeliveryTripDashboard(UnitTestCase):
	def test_transport_connections_are_available(self):
		data = {"non_standard_fieldnames": {}}

		result = get_data(data)

		self.assertEqual(result["non_standard_fieldnames"]["Transport Load"], "delivery_trip")
		self.assertEqual(
			result["non_standard_fieldnames"]["Vehicle Allocation"],
			"vuna_current_vehicle_allocation",
		)
