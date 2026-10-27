# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from unittest.mock import patch

from frappe.tests import UnitTestCase

from vuna_transport.vuna_transport.doctype.vehicle_onboarding.vehicle_onboarding import (
	get_onboarding_template_documents,
)


class TestVehicleOnboardingTemplate(UnitTestCase):
	@patch("vuna_transport.vuna_transport.doctype.vehicle_onboarding.vehicle_onboarding.frappe.get_all")
	@patch("vuna_transport.vuna_transport.doctype.vehicle_onboarding.vehicle_onboarding.frappe.db.get_value")
	@patch(
		"vuna_transport.vuna_transport.doctype.vehicle_onboarding.vehicle_onboarding.require_doctype_permission"
	)
	def test_returns_configured_template_documents(self, require_permission, get_value, get_all):
		get_value.return_value = "VOT-1"
		get_all.return_value = [
			{"vehicle_document_type": "Insurance", "mandatory": 1},
			{"vehicle_document_type": "Inspection Certificate", "mandatory": 0},
		]

		result = get_onboarding_template_documents("Supplier Mileage")

		require_permission.assert_called_once_with("Vehicle Onboarding Template", "read")
		get_value.assert_called_once_with(
			"Vehicle Onboarding Template",
			{"transport_cost_structure": "Supplier Mileage"},
			"name",
		)
		self.assertEqual(result, get_all.return_value)
