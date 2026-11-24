"""Planning and allocation must agree about authorized category allowances."""

from unittest import TestCase
from unittest.mock import patch

import frappe

from vuna_transport.services.fleet import vehicle_category_service as categories
from vuna_transport.services.planning import delivery_load_service as loads
from vuna_transport.services.planning import dispatch_planning_service as planner


class TestCapacityOverridePlanning(TestCase):
	def setUp(self):
		self.category = frappe._dict(
			name="Truck",
			vehicle_category_name="Truck",
			disabled=0,
			max_payload_weight=100,
			max_payload_weight_uom="Kg",
			max_volume=10,
			max_volume_uom="Cubic Meter",
			max_pallets=5,
			max_cases=20,
			max_crates=10,
			capacity_override_percentage=10,
			tail_lift=0,
		)
		self.settings = frappe._dict(allow_capacity_override=1)
		self.enterContext(patch.object(categories.frappe, "get_all", return_value=[self.category]))
		self.enterContext(patch.object(categories.frappe, "get_doc", return_value=self.category))
		self.enterContext(
			patch.object(
				categories.frappe,
				"get_cached_doc",
				side_effect=lambda dt, *a: self.settings if dt == "Transport Settings" else self.category,
			)
		)
		self.allowed = self.enterContext(patch.object(categories.frappe, "has_permission", return_value=True))
		self.enterContext(patch.object(loads, "require_doctype_permission"))
		self.enterContext(patch.object(planner, "require_doctype_permission"))
		self.enterContext(patch.object(loads, "_vehicle_meets_load_access_limits", return_value=True))
		self.load = frappe._dict(
			required_vehicle_category="Truck",
			weight_uom="Kg",
			volume_uom="Cubic Meter",
			total_weight=105,
			total_volume=5,
			total_pallets=0,
			total_cases=0,
			total_crates=0,
			refrigeration_required=0,
			tail_lift_required=0,
			movement_type="Warehouse Transfer",
			delivery_orders=[],
		)

	def test_allowance_is_used_by_choices_recommendations_summary_and_validation(self):
		self.assertEqual(loads.get_compatible_vehicle_category_names(self.load), ["Truck"])
		self.assertEqual(loads.recommend_vehicle_category(self.load), "Truck")
		self.assertTrue(loads.validate_capacity(self.load)["capacity_override_applied"])
		summary = loads.get_vehicle_category_capacity_summary("Truck", {"weight": 105}, "Kg", "Cubic Meter")
		self.assertAlmostEqual(summary["capacity"]["weight"]["capacity"], 110)
		self.assertEqual(summary["capacity"]["weight"]["base_capacity"], 100)

	def test_selected_category_filter_does_not_hide_demands_within_the_allowance(self):
		self.category.as_dict = lambda: dict(self.category)
		rows = [{"weight": 105, "volume": 5, "weight_uom": "Kg", "volume_uom": "Cubic Meter"}]
		self.assertEqual(planner._filter_vehicle_category(rows, "Truck"), rows)

	def test_link_search_includes_categories_eligible_only_with_override(self):
		filters = {"total_weight": 105, "weight_uom": "Kg", "volume_uom": "Cubic Meter"}
		with patch.object(loads, "_vehicle_meets_delivery_order_access_limits", return_value=True):
			rows = loads.get_compatible_vehicle_categories("Vehicle Category", "", "name", 0, 20, filters)
		self.assertEqual(rows, [("Truck", "Truck")])
		self.allowed.return_value = False
		with patch.object(loads, "_vehicle_meets_delivery_order_access_limits", return_value=True):
			self.assertEqual(
				loads.get_compatible_vehicle_categories("Vehicle Category", "", "name", 0, 20, filters), []
			)

	def test_disabled_or_unauthorized_override_is_not_used_anywhere(self):
		for enabled, authorized in ((0, True), (1, False)):
			with self.subTest(enabled=enabled, authorized=authorized):
				self.settings.allow_capacity_override = enabled
				self.allowed.return_value = authorized
				self.assertEqual(loads.get_compatible_vehicle_category_names(self.load), [])
				with self.assertRaises(frappe.ValidationError):
					loads.validate_capacity(self.load)

	def test_zero_allowance_and_exceeded_allowance_are_not_unlimited_capacity(self):
		self.category.capacity_override_percentage = 0
		self.assertEqual(loads.get_compatible_vehicle_category_names(self.load), [])
		self.category.capacity_override_percentage = 10
		self.load.total_weight = 111
		self.assertEqual(loads.get_compatible_vehicle_category_names(self.load), [])
		with self.assertRaises(frappe.ValidationError):
			loads.validate_capacity(self.load)

	def test_repeated_evaluation_never_compounds_the_override(self):
		for _ in range(3):
			categories.configure_category_capacities(self.category, {"weight_uom": "Kg"})
			self.assertAlmostEqual(self.category.capacities["weight"], 110)
			self.assertEqual(self.category.base_capacities["weight"], 100)

	def test_permission_change_reverts_a_previously_evaluated_category(self):
		categories.configure_category_capacities(self.category, {})
		self.allowed.return_value = False
		categories.configure_category_capacities(self.category, {})
		self.assertEqual(self.category.capacities["weight"], 100)
		self.assertFalse(self.category.capacity_override_applied)

	def test_equipment_requirements_are_not_bypassed_by_extra_capacity(self):
		for tail_lift, equipment in ((1, None), (0, "Crane")):
			with self.subTest(tail_lift=tail_lift, equipment=equipment):
				self.load.tail_lift_required = tail_lift
				self.load.special_equipment_required = equipment
				self.assertEqual(loads.get_compatible_vehicle_category_names(self.load), [])
				with self.assertRaises(frappe.ValidationError):
					loads.validate_capacity(self.load)

	def test_refrigeration_is_deferred_to_vehicle_validation_even_with_capacity_override(self):
		self.load.refrigeration_required = 1
		self.assertEqual(loads.get_compatible_vehicle_category_names(self.load), ["Truck"])
		self.assertTrue(loads.validate_capacity(self.load)["capacity_override_applied"])

	def test_shared_categories_apply_allowance_to_all_configured_dimensions(self):
		category = categories.get_vehicle_categories({})[0]
		for key in categories.CAPACITY_DIMENSIONS:
			self.assertAlmostEqual(category.capacities[key], category.base_capacities[key] * 1.1)
		self.assertTrue(category.capacity_override_applied)

	def test_dispatch_preview_exposes_allowance_and_nominal_capacity(self):
		with (
			patch.object(planner, "build_delivery_load_from_allocations", return_value=self.load),
			patch.object(planner, "_validate_planning_load"),
		):
			result = planner.evaluate_manual_load_plan([], "Truck")
		self.assertTrue(result["selected_vehicle_category_compatible"])
		row = result["compatible_vehicle_categories"][0]
		self.assertEqual(row["capacity_override_percentage"], 10)
		self.assertTrue(row["capacity_override_applied"])
		self.assertEqual(row["capacity"]["weight"]["base_capacity"], 100)
		self.assertAlmostEqual(row["capacity"]["weight"]["remaining"], 5)
