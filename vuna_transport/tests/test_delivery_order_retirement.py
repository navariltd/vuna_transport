"""Native retirement preserves source identity and never reopens historical demand."""

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from vuna_transport.services.demand import delivery_order_migration as preparation
from vuna_transport.services.demand import delivery_order_retirement as retirement
from vuna_transport.services.demand import delivery_order_service as legacy_sales
from vuna_transport.services.demand import sales_transport_demand_service as sales
from vuna_transport.tests.test_delivery_order_migration import source_fixture


class TestDeliveryOrderRetirement(unittest.TestCase):
	def test_direct_delivery_note_still_refreshes_native_demand_without_legacy_type(self):
		proof_source = SimpleNamespace(items=[SimpleNamespace(against_sales_order="SO-1")])
		with (
			patch.object(legacy_sales, "frappe") as framework,
			patch.object(sales, "refresh_sales_demands") as refresh,
		):
			framework.db.exists.return_value = False
			legacy_sales.sync_delivery_orders_after_direct_delivery_note(proof_source)
			framework.get_all.assert_not_called()
			refresh.assert_called_once_with(framework.get_doc.return_value)

	def test_historical_stock_unit_proof_does_not_become_cartons(self):
		proof = {
			"name": "old-proof-row",
			"item_code": "Milk",
			"ordered_qty": 36,
			"delivered_qty": 30,
			"rejected_qty": 6,
		}
		note = {
			"item_code": "Milk",
			"qty": 2,
			"stock_qty": 36,
			"uom": "Carton",
			"stock_uom": "Pcs",
			"conversion_factor": 18,
		}
		result = retirement.proof_unit_recovery(proof, note)
		self.assertEqual(
			(result["uom"], result["conversion_factor"], result["stock_qty"], result["rejected_stock_qty"]),
			("Pcs", 1, 30, 6),
		)
		self.assertEqual(proof["delivered_qty"], 30)

	def test_historical_transaction_unit_proof_keeps_cartons(self):
		proof = {
			"name": "old-proof-row",
			"item_code": "Milk",
			"ordered_qty": 2,
			"delivered_qty": 1.5,
			"rejected_qty": 0.5,
		}
		note = {
			"item_code": "Milk",
			"qty": 2,
			"stock_qty": 36,
			"uom": "Carton",
			"stock_uom": "Pcs",
			"conversion_factor": 18,
		}
		result = retirement.proof_unit_recovery(proof, note)
		self.assertEqual(
			(result["uom"], result["conversion_factor"], result["stock_qty"]), ("Carton", 18, 27)
		)
		proof["ordered_qty"] = 7
		with self.assertRaises(preparation.MigrationConflict):
			retirement.proof_unit_recovery(proof, note)

	def fixture(self):
		plan = preparation.build_copy(source_fixture())
		source = {
			"name": "SO-1",
			"docstatus": 1,
			"status": "To Deliver and Bill",
			"items": [
				{
					"name": "so-row-1",
					"item_code": "MILK",
					"uom": "Carton",
					"stock_uom": "Pcs",
					"conversion_factor": 10,
				}
			],
		}
		key = ("2026-11-10", "Address", None, None, None, None, "Company Delivery", "Milk")
		return plan, source, {"so-row-1": key}

	def test_native_projection_preserves_row_identity_and_uom(self):
		plan, source, keys = self.fixture()
		before = copy.deepcopy(plan)
		projected = retirement.project_groups(plan, source, keys)[0]
		self.assertEqual(plan, before)
		self.assertEqual(projected["demand"]["source_document_type"], "Sales Order")
		self.assertEqual(projected["items"][0]["name"], plan["items"][0]["name"])
		self.assertEqual(projected["items"][0]["source_item"], "so-row-1")
		self.assertEqual(projected["items"][0]["stock_qty"], 20)
		self.assertEqual(projected["items"][0]["transaction_uom"], "Carton")

	def test_inactive_and_nonstock_history_cannot_become_dispatch_work(self):
		for status, archived in (("Completed", ()), ("Cancelled", ()), ("To Deliver and Bill", ("MILK",))):
			plan, source, keys = self.fixture()
			source["status"] = status
			target = retirement.project_groups(plan, source, keys, archived)[0]["demand"]
			self.assertEqual(target["migration_archived"], 1)
			self.assertEqual(target["status"], "Closed")

	def test_split_groups_keep_child_ids_and_distinct_source_keys(self):
		plan, source, keys = self.fixture()
		row = copy.deepcopy(plan["items"][0])
		row.update(name="row-2", sales_order_item="so-row-2")
		plan["items"].append(row)
		original = copy.deepcopy(source["items"][0])
		original["name"] = "so-row-2"
		source["items"].append(original)
		keys["so-row-2"] = (*keys["so-row-1"][:-1], "Other")
		groups = retirement.project_groups(plan, source, keys)
		self.assertEqual(len(groups), 2)
		self.assertEqual({r["name"] for g in groups for r in g["items"]}, {"old-row-1", "row-2"})
		self.assertEqual(len({g["demand"]["source_key"] for g in groups}), 2)
		self.assertTrue(all(r["parent"] == g["demand"]["name"] for g in groups for r in g["items"]))

	def test_conflicting_source_uom_blocks_promotion(self):
		plan, source, keys = self.fixture()
		source["items"][0]["conversion_factor"] = 12
		with self.assertRaises(preparation.MigrationConflict):
			retirement.project_groups(plan, source, keys)

	@patch.object(retirement, "frappe")
	def test_upgrade_refuses_to_remove_unconverted_history(self, framework):
		framework.db.exists.return_value = True
		with self.assertRaises(preparation.MigrationConflict):
			retirement.guard_upgrade()
		framework.db.exists.return_value = False
		retirement.guard_upgrade()

	def test_native_path_is_mandatory_after_retirement_even_with_switch_off(self):
		with (
			patch.object(sales.frappe.db, "exists", return_value=False),
			patch.dict(frappe.conf, {"customer_demand_cutover": 0}),
		):
			self.assertTrue(sales.native_customer_demand_enabled())
