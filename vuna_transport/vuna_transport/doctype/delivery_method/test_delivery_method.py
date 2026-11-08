# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

from frappe.exceptions import ValidationError
from frappe.tests import IntegrationTestCase, UnitTestCase

from vuna_transport.services.demand.delivery_method_service import (
	canonical_delivery_method,
	delivery_methods_are_compatible,
	validate_delivery_method_allows_vehicle_allocation,
)

EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


class IntegrationTestDeliveryMethod(IntegrationTestCase):
	pass


class TestDeliveryMethodSemantics(UnitTestCase):
	def test_method_identity_does_not_infer_transport_sourcing(self):
		self.assertEqual(canonical_delivery_method("Company Fleet"), "Company Fleet")
		self.assertFalse(delivery_methods_are_compatible("Company Fleet", "Subcontracted Transport"))

	def test_customer_pickup_remains_incompatible_with_company_delivery(self):
		self.assertFalse(delivery_methods_are_compatible("Customer Pickup", "Company Delivery"))

	@patch("vuna_transport.services.demand.delivery_method_service.frappe.get_cached_doc")
	def test_company_delivery_allows_owned_or_subcontracted_allocation(self, get_method):
		get_method.return_value = SimpleNamespace(
			name="Company Delivery",
			buying=0,
			selling=1,
			transport_arranged_by="Company",
			disabled=0,
			requires_transport_planning=1,
			requires_vehicle_allocation=1,
			requires_delivery_trip=1,
		)
		for allocation_type in ("Owned Fleet", "Subcontractor"):
			workflow = validate_delivery_method_allows_vehicle_allocation("Company Delivery")
			self.assertTrue(workflow.requires_vehicle_allocation, allocation_type)

	@patch("vuna_transport.services.demand.delivery_method_service.frappe.get_cached_doc")
	def test_customer_pickup_rejects_vehicle_allocation(self, get_method):
		get_method.return_value = SimpleNamespace(
			name="Customer Pickup",
			buying=0,
			selling=1,
			transport_arranged_by="Counterparty",
			disabled=0,
			requires_transport_planning=0,
			requires_vehicle_allocation=0,
			requires_delivery_trip=0,
		)
		with self.assertRaises(ValidationError):
			validate_delivery_method_allows_vehicle_allocation("Customer Pickup")
