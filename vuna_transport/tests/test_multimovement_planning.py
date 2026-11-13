"""All dispatch tabs preview their authoritative movement without reserving stock."""

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from vuna_transport.services.planning import dispatch_planning_service as planner
from vuna_transport.services.planning import transport_load_service as loads
from vuna_transport.services.planning.delivery_load_service import build_delivery_load_from_allocations


class TestMultiMovementPlanning(TestCase):
	def test_depot_and_supplier_previews_use_their_own_validator(self):
		for movement in ("Warehouse Transfer", "Supplier Pickup"):
			with (
				self.subTest(movement=movement),
				patch.object(loads, "validate_demand_allocations") as native,
				patch.object(planner, "validate_load_allocations") as customer,
				patch.object(planner, "calculate_load_totals") as totals,
			):
				load = frappe._dict(movement_type=movement)
				planner._validate_planning_load(load)
				native.assert_called_once_with(load)
				customer.assert_not_called()
				# These validators already calculate movement-specific stops and totals.
				totals.assert_not_called()

	def test_customer_preview_keeps_its_existing_validator(self):
		load = frappe._dict(movement_type="Customer Delivery")
		with (
			patch.object(planner, "validate_load_allocations") as validate,
			patch.object(planner, "calculate_load_totals") as totals,
		):
			planner._validate_planning_load(load)
			validate.assert_called_once_with(load, lock=False)
			totals.assert_called_once_with(load)

	def test_generic_builder_is_unsaved_and_keeps_requested_quantity_and_category(self):
		for movement in ("Warehouse Transfer", "Supplier Pickup"):
			with (
				self.subTest(movement=movement),
				patch.object(loads, "frappe") as framework,
				patch.object(loads, "require_company_access") as company,
			):
				first = MagicMock(
					company="Company",
					movement_type=movement,
					source_warehouse="Main",
					required_date="2026-11-13",
				)
				framework.get_doc.return_value = first
				load = framework.new_doc.return_value
				result = loads.build_transport_load_from_demands(
					[{"transport_demand": "TD", "transport_demand_item": "DI", "allocated_quantity": 1.5}],
					"Canter",
					"Loading",
				)
				self.assertIs(result, load)
				first.check_permission.assert_called_once_with("read")
				company.assert_called_once_with("Company")
				self.assertEqual(load.required_vehicle_category, "Canter")
				self.assertEqual(load.source_warehouse, None if movement == "Supplier Pickup" else "Loading")
				self.assertEqual(load.append.call_args.args[1]["allocated_quantity"], 1.5)
				load.insert.assert_not_called()
				load.save.assert_not_called()

	def test_shared_builder_routes_non_customer_demands_without_legacy_links(self):
		allocations = [{"transport_demand": "TD", "transport_demand_item": "DI", "allocated_quantity": 2}]
		with (
			patch("vuna_transport.services.planning.delivery_load_service.frappe") as framework,
			patch.object(loads, "build_transport_load_from_demands") as build,
		):
			framework.get_doc.return_value.movement_type = "Supplier Pickup"
			result = build_delivery_load_from_allocations(allocations, "Canter", "Ignored")
			self.assertIs(result, build.return_value)
			build.assert_called_once_with(allocations, "Canter", "Ignored")

	def test_capacity_and_cost_previews_use_same_movement_validator(self):
		load = frappe._dict(movement_type="Warehouse Transfer", delivery_orders=[])
		with (
			patch.object(planner, "require_doctype_permission"),
			patch.object(planner, "build_delivery_load_from_allocations", return_value=load),
			patch.object(loads, "validate_demand_allocations") as validate,
			patch.object(planner, "get_compatible_vehicle_category_names", return_value=[]),
			patch.object(planner, "get_vehicle_categories", return_value=[]),
			patch.object(planner, "get_capacity_buffer_percentage", return_value=0),
			patch.object(planner, "_cost_structure_estimates_for_load", return_value={"cost_structures": []}),
		):
			planner.evaluate_manual_load_plan([{"transport_demand": "TD"}])
			planner.get_cost_structure_estimates([{"transport_demand": "TD"}])
			self.assertEqual(validate.call_count, 2)
