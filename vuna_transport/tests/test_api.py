from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from vuna_transport.api import get_drivers_for_allocation


class TestVehicleAllocationDriverSearch(UnitTestCase):
	@patch("vuna_transport.api.frappe.get_list")
	@patch("vuna_transport.api.get_driver_availability_map")
	@patch("vuna_transport.api.get_allocation_resource_window")
	def test_searches_currently_available_drivers_without_delivery_loads(
		self, get_resource_window, get_availability_map, get_list
	):
		def get_driver_rows(doctype, **kwargs):
			if doctype != "Driver":
				return []
			if kwargs.get("pluck") == "name":
				return ["DR-001"]
			return [("DR-001", "Jane Driver", "0700000000", "DL-001")]

		get_list.side_effect = get_driver_rows
		get_resource_window.return_value = ("2026-10-27 08:00:00", "2026-10-27 08:00:01")
		get_availability_map.return_value = {"DR-001": frappe._dict(driver="DR-001", dispatch_eligible=True)}

		result = get_drivers_for_allocation(
			"Driver", "", "name", 0, 20, {"company": "Yovuna Tech", "delivery_loads": []}
		)

		self.assertEqual(result, [("DR-001", "Jane Driver", "0700000000", "DL-001")])
		availability_arguments = get_availability_map.call_args.kwargs
		self.assertEqual(availability_arguments["company"], "Yovuna Tech")
		self.assertEqual(availability_arguments["driver_names"], ["DR-001"])
