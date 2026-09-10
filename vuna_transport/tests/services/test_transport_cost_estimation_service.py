# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests import UnitTestCase

from vuna_transport.services.costing.transport_cost_estimation_service import (
	CostContext,
	estimate_cost_component,
)


class TestTransportCostEstimationService(UnitTestCase):
	def setUp(self):
		self.vehicle = SimpleNamespace(
			name="KDA 123A",
			vuna_expected_fuel_efficiency=5,
			vuna_fuel_efficiency_uom="Kilometer",
		)
		self.context = CostContext(
			company="Yovuna Tech",
			load=SimpleNamespace(name="DL-001"),
			vehicle=self.vehicle,
			date="2026-09-09",
			distance=300,
			distance_uom="Kilometer",
			distance_source="Planned Distance",
			destination="Nairobi",
			destinations=["Nairobi"],
		)

	def test_per_distance_component(self):
		component = SimpleNamespace(
			cost_component="Distance Charge",
			calculation_basis="Per Distance",
			cost_nature="Variable",
			rate=75,
			rate_uom="Kilometer",
		)

		result = estimate_cost_component(component, self.context)

		self.assertEqual(result["quantity"], 300)
		self.assertEqual(result["amount"], 22500)
		self.assertEqual(result["uom"], "Kilometer")

	def test_fuel_component_uses_distance_and_efficiency(self):
		component = SimpleNamespace(
			cost_component="Fuel",
			calculation_basis="Per Fuel Quantity",
			cost_nature="Variable",
			rate=185,
			rate_uom="Litre",
		)

		result = estimate_cost_component(component, self.context)

		self.assertEqual(result["quantity"], 60)
		self.assertEqual(result["amount"], 11100)

	def test_monthly_fixed_component_is_marked_separately(self):
		component = SimpleNamespace(
			cost_component="Monthly Contract",
			calculation_basis="Monthly Fixed",
			cost_nature="Fixed",
			flat_amount=250000,
		)

		result = estimate_cost_component(component, self.context)

		self.assertEqual(result["amount"], 250000)
		self.assertIn("Monthly Fixed", result["warnings"][0])

	@patch("vuna_transport.services.costing.transport_cost_estimation_service.get_transport_uoms")
	@patch("vuna_transport.services.costing.transport_cost_estimation_service.require_company_access")
	@patch("vuna_transport.services.costing.transport_cost_estimation_service.require_document_permission")
	@patch("vuna_transport.services.costing.transport_cost_estimation_service.frappe.get_doc")
	def test_missing_profile_returns_warning_without_mutation(
		self, get_doc, _require_document_permission, _require_company_access, get_uoms
	):
		from vuna_transport.services.costing.transport_cost_estimation_service import estimate_transport_cost

		load = SimpleNamespace(
			doctype="Delivery Load",
			name="DL-001",
			company="Yovuna Tech",
			dispatch_date="2026-09-09",
			estimated_distance=300,
			delivery_orders=[],
		)
		vehicle = SimpleNamespace(
			doctype="Vehicle",
			name="KDA 123A",
			company="Yovuna Tech",
			vuna_transport_costing_model="Mileage",
			vuna_transport_cost_profile=None,
			vuna_vehicle_category="8 Ton Truck",
		)
		get_doc.side_effect = [load, vehicle]
		get_uoms.return_value = SimpleNamespace(distance_uom="Kilometer")

		with patch(
			"vuna_transport.services.costing.transport_cost_estimation_service.frappe.get_all",
			return_value=[],
		):
			result = estimate_transport_cost("DL-001", "KDA 123A")

		self.assertEqual(result["estimated_full_cost"], 0)
		self.assertTrue(any("No active Transport Cost Profile" in warning for warning in result["warnings"]))

	@patch("vuna_transport.services.costing.transport_cost_estimation_service.frappe.get_doc")
	@patch("vuna_transport.services.costing.transport_cost_estimation_service.frappe.get_all")
	def test_profile_resolution_is_scoped_to_vehicle_costing_model(self, get_all, get_doc):
		from vuna_transport.services.costing.transport_cost_estimation_service import (
			get_applicable_cost_profile,
		)

		vehicle = SimpleNamespace(
			name="KDA 123A",
			vuna_transport_costing_model="Mileage",
			vuna_transport_cost_profile=None,
			vuna_vehicle_category="8 Ton Truck",
			vuna_supplier=None,
		)
		get_all.return_value = [
			SimpleNamespace(
				name="Mileage Company Profile",
				company="Yovuna Tech",
				vehicle=None,
				supplier=None,
				vehicle_category=None,
				disabled=0,
				valid_from="2026-01-01",
				valid_to=None,
			)
		]
		profile = SimpleNamespace(
			name="Mileage Company Profile",
			costing_model="Mileage",
			company="Yovuna Tech",
			disabled=0,
			valid_from="2026-01-01",
			valid_to=None,
		)
		get_doc.return_value = profile

		result = get_applicable_cost_profile(vehicle, "Yovuna Tech", "2026-09-10")

		self.assertIs(result, profile)
		get_all.assert_called_once_with(
			"Transport Cost Profile",
			filters={"company": "Yovuna Tech", "costing_model": "Mileage", "disabled": 0},
			fields=["name", "vehicle", "supplier", "vehicle_category", "valid_from", "valid_to"],
		)

	@patch("vuna_transport.services.costing.transport_cost_estimation_service.frappe.get_doc")
	def test_direct_profile_with_different_costing_model_is_rejected(self, get_doc):
		from vuna_transport.services.costing.transport_cost_estimation_service import (
			get_applicable_cost_profile,
		)

		vehicle = SimpleNamespace(
			name="KDA 123A",
			vuna_transport_costing_model="Mileage",
			vuna_transport_cost_profile="Contract Profile",
		)
		get_doc.return_value = SimpleNamespace(name="Contract Profile", costing_model="Contract")

		with self.assertRaises(Exception):
			get_applicable_cost_profile(vehicle, "Yovuna Tech", "2026-09-10")
