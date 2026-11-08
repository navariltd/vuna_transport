import unittest
from unittest.mock import patch

import frappe

from vuna_transport.services.fleet import vehicle_allocation_service as service


def run_site_check(load_name: str, vehicle_category: str):
	"""Evaluate existing cargo without creating, saving or allocating anything."""
	load = frappe.get_doc("Transport Load", load_name)
	try:
		service._validate_vehicle_category_for_load(load, vehicle_category)
	except frappe.ValidationError as error:
		return {"compatible": False, "reason": str(error), "writes": 0}
	return {
		"compatible": True,
		"recommended_category": load.required_vehicle_category,
		"selected_category": vehicle_category,
		"writes": 0,
	}


def cargo_load(category="FH", name="TL-1"):
	return frappe._dict(
		name=name,
		company="Company",
		required_vehicle_category=category,
		delivery_method=None,
		weight_uom="Kg",
		volume_uom=None,
		total_weight=100,
		total_volume=0,
		total_pallets=0,
		total_cases=0,
		total_crates=0,
		refrigeration_required=1,
		tail_lift_required=0,
		required_body_type="Refrigerated",
		special_equipment_required="Temperature Logger",
		delivery_orders=[],
	)


class TestAllocationCategoryPolicy(unittest.TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(service, "frappe"))
		self.framework.throw.side_effect = ValueError
		self.framework._dict.side_effect = frappe._dict
		self.enterContext(patch.object(service, "_", side_effect=lambda value: value))
		self.capacity = self.enterContext(
			patch("vuna_transport.services.planning.delivery_load_service.validate_capacity")
		)
		self.minimums = self.enterContext(
			patch("vuna_transport.services.fleet.vehicle_category_service.validate_minimum_load_requirements")
		)
		self.framework.get_doc.return_value = frappe._dict(name="Canter")

	def test_different_recommended_category_does_not_reject_actual_vehicle(self):
		service._validate_vehicle_category_for_loads([cargo_load()], "Canter")
		combined, category = self.capacity.call_args.args
		self.assertEqual(category.name, "Canter")
		self.assertEqual(combined.total_weight, 100)
		self.minimums.assert_called_once()

	def test_special_requirements_are_still_passed_to_capacity_validator(self):
		service._validate_vehicle_category_for_loads([cargo_load()], "Canter")
		combined = self.capacity.call_args.args[0]
		self.assertTrue(combined.refrigeration_required)
		self.assertEqual(combined.required_body_type, "Refrigerated")
		self.assertEqual(combined.special_equipment_required, "Temperature Logger")

	def test_actual_capacity_or_equipment_failure_is_still_enforced(self):
		self.capacity.side_effect = ValueError("Refrigeration required")
		with self.assertRaises(ValueError):
			service._validate_vehicle_category_for_loads([cargo_load()], "Canter")

	def test_loads_with_different_recommendations_can_be_combined(self):
		with patch(
			"vuna_transport.services.planning.load_validation_service.validate_transport_compatibility_groups"
		):
			service._validate_selected_load_compatibility([cargo_load(), cargo_load("Canter", "TL-2")])

	def test_actual_vehicle_category_replaces_stale_recommendation(self):
		vehicle = frappe._dict(name="VEH-1", vuna_vehicle_owner="Company", vuna_vehicle_category="Canter")
		self.framework.get_doc.return_value = vehicle
		allocation = frappe._dict(vehicle="VEH-1", required_vehicle_category="FH", cost_structure=None)
		service._derive_vehicle_source(allocation)
		self.assertEqual(allocation.required_vehicle_category, "Canter")
