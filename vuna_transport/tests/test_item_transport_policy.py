"""Item + source workflow eligibility, without changing sales or inventory rows."""

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from vuna_transport import install
from vuna_transport.services.demand import item_transport_policy as policy
from vuna_transport.services.demand import sales_transport_demand_service as sales
from vuna_transport.services.demand import supplier_pickup_demand_service as buying
from vuna_transport.services.demand import transport_demand_service as demands
from vuna_transport.tests.test_supplier_pickup_demand_service import purchase_order
from vuna_transport.tests.test_transport_demand_service import transfer_source


class TestItemTransportPolicy(unittest.TestCase):
	def setUp(self):
		self.items = {
			"MILK": frappe._dict(is_stock_item=1, disabled=0, vuna_requires_transport=1),
			"PACK": frappe._dict(is_stock_item=1, disabled=0, vuna_requires_transport=1),
			"EXCLUDED": frappe._dict(is_stock_item=1, disabled=0, vuna_requires_transport=0),
			"SERVICE": frappe._dict(is_stock_item=0, disabled=0, vuna_requires_transport=1),
		}
		self.master = self.enterContext(
			patch.object(policy.frappe, "get_cached_doc", side_effect=lambda dt, name: self.items[name])
		)

	def test_only_active_stock_items_with_the_checkbox_enabled_are_eligible(self):
		self.assertTrue(policy.item_requires_transport("MILK"))
		for code in ("EXCLUDED", "SERVICE", None):
			self.assertFalse(policy.item_requires_transport(code))
		self.items["MILK"].disabled = 1
		self.assertFalse(policy.item_requires_transport("MILK"))

	def test_pre_upgrade_items_and_string_checkbox_values_are_handled(self):
		self.items["MILK"].pop("vuna_requires_transport")
		self.assertTrue(policy.item_requires_transport("MILK"))
		for value, expected in (("0", False), ("1", True), (0, False), (1, True)):
			self.items["MILK"].vuna_requires_transport = value
			self.assertEqual(policy.item_requires_transport("MILK"), expected)

	def test_mixed_material_request_keeps_only_transport_rows_in_requested_uom(self):
		source = transfer_source()
		source["items"].append(
			frappe._dict(
				{
					**source["items"][0],
					"name": "excluded",
					"item_code": "EXCLUDED",
					"warehouse": None,
					"conversion_factor": 0,
				}
			)
		)
		before = copy.deepcopy(source)
		leg = demands.build_source_legs(source)[0]
		self.assertEqual([row["source_item"] for row in leg["items"]], ["row-1"])
		self.assertEqual(
			(leg["items"][0]["transaction_qty"], leg["items"][0]["transaction_uom"]), (8, "Carton")
		)
		self.assertEqual(source, before)

	def test_all_excluded_material_request_has_no_demand_legs(self):
		source = transfer_source()
		source["items"][0].item_code = "EXCLUDED"
		self.assertEqual(demands.build_source_legs(source), [])

	def test_existing_material_request_demand_survives_a_later_item_opt_out(self):
		source = transfer_source()
		self.items["MILK"].vuna_requires_transport = 0
		self.assertEqual(demands.build_source_legs(source), [])
		self.assertEqual(len(demands.build_source_legs(source, preserved_source_items={"row-1"})), 1)

	def test_all_excluded_manual_generation_returns_empty_without_inserting(self):
		source = transfer_source()
		source["items"][0].item_code = "EXCLUDED"
		source.check_permission, source.reload = Mock(), Mock()
		with (
			patch.object(demands, "frappe") as framework,
			patch.object(demands, "require_company_access"),
			patch("vuna_transport.services.demand.source_policy_service.require_source_enabled"),
		):
			framework.get_doc.return_value = source
			self.assertEqual(demands._create_transport_demands("Material Request", source.name), [])
			framework.get_doc.assert_called_once_with("Material Request", source.name)

	def sales_context(self):
		self.enterContext(patch.object(sales.sales, "_requires_transport_planning", return_value=True))
		self.enterContext(
			patch.object(
				sales.sales, "_is_stock_item", side_effect=lambda row: self.items[row.item_code].is_stock_item
			)
		)
		self.enterContext(patch.object(sales.sales, "_get_transport_quantity", return_value=2))
		self.enterContext(
			patch.object(
				sales.sales,
				"_get_group_key",
				return_value=("2026-11-16", "Address", None, None, None, None, "Company Delivery", None),
			)
		)
		self.enterContext(patch.object(sales, "get_address_planning_data", return_value=frappe._dict()))
		self.enterContext(patch.object(sales, "get_warehouse_planning_data", return_value=frappe._dict()))
		self.enterContext(
			patch.object(
				sales.sales,
				"build_sales_planning_snapshot",
				side_effect=lambda source, key, rows: {
					"items": [
						{
							"sales_order_item": row.name,
							"item_code": row.item_code,
							"transport_qty": qty,
							"uom": "Carton",
							"conversion_factor": 10,
							"stock_qty": qty * 10,
							"stock_uom": "Pcs",
						}
						for row, qty in rows
					]
				},
			)
		)
		return SimpleNamespace(
			name="SO",
			company="Company",
			customer="Customer",
			docstatus=1,
			status="To Deliver and Bill",
			items=[
				SimpleNamespace(name=code, item_code=code, conversion_factor=10)
				for code in ("MILK", "EXCLUDED", "SERVICE")
			],
		)

	def test_mixed_sales_order_demands_only_eligible_rows_and_leaves_source_untouched(self):
		source = self.sales_context()
		rows = list(source.items)
		legs = sales.build_sales_legs(source)
		self.assertEqual([row["source_item"] for row in legs[0]["items"]], ["MILK"])
		self.assertEqual(source.items, rows)

	def test_item_checkbox_does_not_override_the_delivery_method(self):
		source = self.sales_context()
		with patch.object(sales.sales, "_requires_transport_planning", return_value=False):
			self.assertEqual(sales.build_sales_legs(source), [])

	def test_all_excluded_sales_and_existing_sales_demand_are_distinct(self):
		source = self.sales_context()
		self.items["MILK"].vuna_requires_transport = 0
		self.assertEqual(sales.build_sales_legs(source), [])
		legs = sales.build_sales_legs(source, {"MILK"}, preserved_source_items={"MILK"})
		self.assertEqual([row["source_item"] for row in legs[0]["items"]], ["MILK"])

	def test_no_transport_supplier_items_do_not_require_a_pickup_address(self):
		source = purchase_order()
		source.dispatch_address = source.supplier_address = None
		source["items"][0].item_code = "EXCLUDED"
		with patch.object(buying, "is_company_pickup_order", return_value=True):
			self.assertEqual(buying.build_supplier_pickup_legs(source), [])

	def test_return_pickup_demand_does_not_apply_the_new_order_item_filter(self):
		source = frappe._dict(doctype="Return Request", docstatus=1, status="Awaiting Pickup")
		with patch(
			"vuna_transport.services.demand.depot_return_demand_service.build_depot_return_legs",
			return_value=[{"items": []}],
		) as returns:
			self.assertEqual(demands.build_source_legs(source), [{"items": []}])
			returns.assert_called_once_with(source)
		self.master.assert_not_called()

	def test_mixed_supplier_order_keeps_only_eligible_rows_and_preserves_existing_work(self):
		source = purchase_order()
		source["items"].append(
			frappe._dict(
				{
					**source["items"][0],
					"name": "EXCLUDED-ROW",
					"item_code": "EXCLUDED",
					"warehouse": None,
					"conversion_factor": 0,
				}
			)
		)
		warehouse = frappe._dict(company="Company", disabled=0, is_group=0, warehouse_type="Stores")
		with (
			patch.object(buying, "is_company_pickup_order", return_value=True),
			patch.object(buying, "validate_pickup_location"),
			patch.object(buying.frappe, "get_doc", return_value=warehouse),
			patch.object(buying.frappe, "get_cached_value", return_value=1),
		):
			leg = buying.build_supplier_pickup_legs(source)[0]
			self.assertEqual([row["source_item"] for row in leg["items"]], ["POI-1"])
			self.assertEqual(
				(leg["items"][0]["transaction_qty"], leg["items"][0]["transaction_uom"]), (15, "Carton")
			)
			self.items["PACK"].vuna_requires_transport = 0
			self.assertEqual(buying.build_supplier_pickup_legs(source), [])
			leg = buying.build_supplier_pickup_legs(source, preserved_source_items={"POI-1"})[0]
			self.assertEqual([row["source_item"] for row in leg["items"]], ["POI-1"])

	def test_upgrade_installs_only_the_new_default_enabled_field(self):
		with patch("frappe.custom.doctype.custom_field.custom_field.create_custom_fields") as create:
			install.ensure_item_transport_requirement()
		fields = create.call_args.args[0]
		self.assertEqual(set(fields), {"Item"})
		self.assertEqual(len(fields["Item"]), 1)
		self.assertEqual(fields["Item"][0]["fieldname"], "vuna_requires_transport")
		self.assertEqual(fields["Item"][0]["default"], "1")
