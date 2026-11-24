"""Cutover metadata, security boundaries and canonical planner writes."""

import unittest
from unittest.mock import patch

import frappe

from vuna_transport.services.planning import customer_planning_source as source
from vuna_transport.services.planning import dispatch_filter_service as filters
from vuna_transport.services.planning import dispatch_planning_service as planner
from vuna_transport.services.planning.delivery_load_service import build_delivery_load_from_allocations


class TestCustomerPlanningScreens(unittest.TestCase):
	def test_summary_reports_stale_grouping_without_hiding_healthy_demands(self):
		from types import SimpleNamespace

		from vuna_transport.services.planning.customer_demand_load_service import (
			CustomerDemandGroupingChanged,
		)

		mute = frappe.flags.mute_messages
		with (
			patch.object(
				source.frappe,
				"get_doc",
				side_effect=[
					SimpleNamespace(name=name, check_permission=lambda mode: None, source_warehouse=None)
					for name in ("STALE", "HEALTHY")
				],
			),
			patch.object(source.frappe.db, "sql", return_value=[]),
			patch(
				"vuna_transport.services.planning.customer_demand_load_service.available_items",
				side_effect=[CustomerDemandGroupingChanged("Review STALE"), {}],
			),
		):
			result = source.metrics(["STALE", "HEALTHY"], tolerate_stale_grouping=True)
		self.assertEqual(result["STALE"], {"planning_issue": "Review STALE"})
		self.assertEqual(result["HEALTHY"]["unallocated_quantity"], 0)
		self.assertEqual(frappe.flags.mute_messages, mute)

	def test_detail_validation_stays_strict_and_other_failures_are_not_swallowed(self):
		from types import SimpleNamespace

		from vuna_transport.services.planning.customer_demand_load_service import (
			CustomerDemandGroupingChanged,
		)

		for error, tolerant in (
			(CustomerDemandGroupingChanged("stale"), False),
			(frappe.PermissionError("denied"), True),
			(frappe.ValidationError("invalid UOM"), True),
			(RuntimeError("unexpected"), True),
		):
			with (
				self.subTest(error=type(error).__name__, tolerant=tolerant),
				patch.object(
					source.frappe,
					"get_doc",
					return_value=SimpleNamespace(check_permission=lambda mode: None),
				),
				patch(
					"vuna_transport.services.planning.customer_demand_load_service.available_items",
					side_effect=error,
				),
				self.assertRaises(type(error)),
			):
				source.metrics(["TD-1"], tolerate_stale_grouping=tolerant)

	def test_one_consolidated_note_can_cover_multiple_demands(self):
		from types import SimpleNamespace

		from vuna_transport.services.execution import execution_service as execution

		load = SimpleNamespace(docstatus=1, status="Ready for Dispatch")
		note = SimpleNamespace(docstatus=1)
		with (
			patch.object(execution, "_requires_vehicle_allocation", return_value=False),
			patch.object(execution, "_requires_delivery_trip", return_value=True),
		):
			action = execution._get_next_action(
				load,
				None,
				None,
				{"TD-1", "TD-2"},
				[note],
				[note],
				[],
				None,
				[],
				[],
				[],
				"Before Delivery Notes",
				True,
			)
			self.assertEqual(action["key"], "create_delivery_trip")

	def test_context_and_aliases_follow_site_mode_not_browser_input(self):
		for mode, target, parent in (
			(0, "Delivery Order", "delivery_order"),
			(1, "Transport Demand", "transport_demand"),
		):
			with (
				patch.dict(frappe.conf, {"customer_demand_cutover": mode}),
				patch.object(frappe.db, "exists", return_value=True),
				patch.object(frappe.db, "get_single_value", return_value=1),
				patch.object(planner, "require_doctype_permission"),
			):
				context = planner.get_customer_planning_context()
				self.assertEqual(context["doctype"], target)
				self.assertEqual(context["order_field"], parent)
				self.assertTrue(context["costing_enabled"])
				self.assertEqual(
					source.field("delivery_address"), "destination_address" if mode else "delivery_address"
				)
				self.assertEqual(
					source.fields(["required_delivery_date"]),
					["required_date as required_delivery_date"] if mode else ["required_delivery_date"],
				)

	def test_native_filters_cannot_include_depot_or_prepared_legacy_wrappers(self):
		with (
			patch.dict(frappe.conf, {"customer_demand_cutover": 1}),
			patch.object(source.frappe, "get_all", return_value=["SO-1"]),
		):
			conditions = source.filters({"company": "A", "sales_order": ["in", ["SO-1", "SO-2"]]})
			self.assertEqual(conditions["source_document_type"], "Sales Order")
			self.assertEqual(conditions["movement_type"], "Customer Delivery")
			self.assertEqual(conditions["sales_order"], ["in", ["SO-1"]])

	def test_filter_group_uses_native_metadata_and_rejects_scope_overrides(self):
		with patch.dict(frappe.conf, {"customer_demand_cutover": 1}):
			self.assertEqual(
				filters.validate_additional_filters([["Transport Demand", "name", "=", "TD-1"]]),
				[["Transport Demand", "name", "=", "TD-1"]],
			)
			for field in (
				"company",
				"docstatus",
				"status",
				"movement_type",
				"source_document_type",
				"source_document",
			):
				with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
					filters.validate_additional_filters([["Transport Demand", field, "=", "arbitrary"]])
			with self.assertRaises(frappe.ValidationError):
				filters.validate_additional_filters([["Delivery Order", "name", "=", "DO-1"]])

	def test_native_manual_builder_never_populates_legacy_links(self):
		from types import SimpleNamespace

		demand = SimpleNamespace(
			name="TD-1",
			movement_type="Customer Delivery",
			check_permission=lambda *args: None,
			company="A",
			source_warehouse=None,
			required_date="2026-11-10",
			delivery_method=None,
			transport_zone=None,
			preferred_route=None,
		)
		get_doc = frappe.get_doc
		with (
			patch(
				"frappe.get_doc",
				side_effect=lambda *args, **kwargs: demand
				if args == ("Transport Demand", "TD-1")
				else get_doc(*args, **kwargs),
			),
			patch(
				"vuna_transport.services.planning.customer_demand_load_service._read_demand",
				return_value=demand,
			),
		):
			load = build_delivery_load_from_allocations(
				[{"transport_demand": "TD-1", "transport_demand_item": "DI-1", "allocated_quantity": 2}],
				source_warehouse="Loading A",
			)
			self.assertEqual(load.movement_type, "Customer Delivery")
			self.assertEqual(load.source_warehouse, "Loading A")
			self.assertEqual(load.delivery_orders[0].transport_demand_item, "DI-1")
			self.assertFalse(load.delivery_orders[0].delivery_order)
			self.assertFalse(load.delivery_orders[0].delivery_order_item)

	def test_mixed_or_incomplete_native_rows_cannot_fall_back_to_legacy_builder(self):
		for rows in (
			[{"transport_demand": "TD-1", "allocated_quantity": 1}],
			[{"transport_demand": "TD-1", "transport_demand_item": "DI-1", "delivery_order": "DO-1"}],
		):
			with self.subTest(rows=rows), self.assertRaises(frappe.ValidationError):
				build_delivery_load_from_allocations(rows)

	def test_detail_api_rejects_partially_permitted_selection(self):
		with (
			patch.dict(frappe.conf, {"customer_demand_cutover": 1}),
			patch.object(planner, "require_doctype_permission"),
			patch.object(planner.frappe, "get_list", return_value=[]),
			patch.object(source.frappe, "get_all", return_value=[]),
		):
			with self.assertRaises(frappe.PermissionError):
				planner.get_delivery_order_details(["hidden-demand"])

	def test_metrics_reject_foreign_loading_warehouse(self):
		from types import SimpleNamespace

		demand = SimpleNamespace(
			company="A", check_permission=lambda mode: None, source_warehouse=None, name="TD-1", items=[]
		)
		warehouse = SimpleNamespace(company="B", check_permission=lambda mode: None, disabled=0, is_group=0)
		with (
			patch.object(source.frappe, "get_doc", side_effect=[demand, warehouse]),
			patch.object(source.frappe.db, "sql", return_value=[]),
			patch(
				"vuna_transport.services.planning.customer_demand_load_service.available_items",
				return_value={},
			),
		):
			with self.assertRaises(frappe.ValidationError):
				source.metrics(["TD-1"], source_warehouse="Foreign")

	def test_item_stock_uses_selected_warehouse_and_preserves_transaction_units(self):
		from types import SimpleNamespace

		item = frappe._dict(
			name="DI-1",
			item_code="Milk",
			item_name="Milk",
			transaction_qty=2,
			transaction_uom="Carton",
			stock_uom="Pcs",
			conversion_factor=10,
			weight_uom="Kg",
			volume_uom="Litre",
		)
		demand = SimpleNamespace(
			company="A",
			check_permission=lambda mode: None,
			source_warehouse="Preferred",
			name="TD-1",
			items=[item],
		)
		warehouse = SimpleNamespace(company="A", check_permission=lambda mode: None, disabled=0, is_group=0)
		with (
			patch.object(source.frappe, "get_doc", side_effect=[demand, warehouse]) as documents,
			patch.object(source.frappe.db, "sql", return_value=[]),
			patch.object(
				source.frappe, "get_all", return_value=[frappe._dict(item_code="Milk", actual_qty=120)]
			) as bins,
			patch(
				"vuna_transport.services.planning.customer_demand_load_service.available_items",
				return_value={"DI-1": {"row": item, "available_quantity": 2, "available_stock_qty": 20}},
			),
		):
			result = source.metrics(["TD-1"], include_items=True, source_warehouse="Selected")["TD-1"][
				"items"
			][0]
			self.assertEqual(documents.call_args.args, ("Warehouse", "Selected"))
			self.assertEqual(bins.call_args.kwargs["filters"]["warehouse"], "Selected")
			self.assertEqual(result["stock_warehouse"], "Selected")
			self.assertEqual(result["stock_available_quantity"], 120)
			self.assertEqual(result["stock_required_quantity"], 20)
			self.assertEqual(result["available_quantity"], 2)
			self.assertEqual(result["uom"], "Carton")

	def test_item_stock_without_warehouse_is_unknown_not_insufficient(self):
		from types import SimpleNamespace

		item = frappe._dict(
			name="DI-1",
			item_code="Milk",
			transaction_qty=2,
			transaction_uom="Carton",
			stock_uom="Pcs",
			conversion_factor=10,
		)
		demand = SimpleNamespace(
			company="A", check_permission=lambda mode: None, source_warehouse=None, name="TD-1", items=[item]
		)
		with (
			patch.object(source.frappe, "get_doc", return_value=demand),
			patch.object(source.frappe.db, "sql", return_value=[]),
			patch.object(source.frappe, "get_all") as bins,
			patch(
				"vuna_transport.services.planning.customer_demand_load_service.available_items",
				return_value={"DI-1": {"row": item, "available_quantity": 2, "available_stock_qty": 20}},
			),
		):
			result = source.metrics(["TD-1"], include_items=True)["TD-1"]["items"][0]
			self.assertFalse(result["stock_warehouse"])
			self.assertEqual(result["stock_availability_status"], "Select Warehouse")
			bins.assert_not_called()
