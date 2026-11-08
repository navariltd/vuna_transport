"""Preparation recovers missing source units without editing original records."""

import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from vuna_transport.services.demand import delivery_order_migration as migration
from vuna_transport.tests.test_delivery_order_migration import source_fixture


class TestMigrationUOMRecovery(unittest.TestCase):
	def setUp(self):
		self.source = source_fixture()
		self.source["items"][0].update(uom=None, conversion_factor=0, stock_qty=0)
		self.sales = {
			"name": "SO-1",
			"company": "Dairy",
			"customer": "Customer",
			"docstatus": 1,
			"items": [
				{
					"name": "so-row-1",
					"parent": "SO-1",
					"parenttype": "Sales Order",
					"item_code": "MILK",
					"qty": 3,
					"uom": "Carton",
					"conversion_factor": 10,
					"stock_qty": 30,
					"stock_uom": "Pcs",
				}
			],
		}

	def test_recovers_source_uom_without_changing_original_evidence(self):
		before = copy.deepcopy(self.source)
		plan = migration.build_copy(self.source, sales_order=self.sales)
		row = plan["items"][0]
		self.assertEqual(
			(row["transaction_qty"], row["transaction_uom"], row["conversion_factor"], row["stock_qty"]),
			(2, "Carton", 10, 20),
		)
		self.assertEqual((row["reserved_stock_qty"], row["allocated_stock_qty"]), (5, 10))
		self.assertEqual(plan["demand"]["legacy_delivery_order_checksum"], migration.checksum(before))
		self.assertEqual(self.source, before)
		self.assertEqual(plan["source"], before)
		self.assertEqual(plan["uom_recoveries"][0]["before"]["conversion_factor"], 0)
		self.assertEqual(plan["uom_recoveries"][0]["sales_order_item"], "so-row-1")

	def test_known_consistent_partial_values_are_retained(self):
		self.source["items"][0].update(uom="Carton", stock_qty=20)
		resolved, _ = migration.recover_missing_uoms(self.source, self.sales)
		self.assertEqual(resolved["items"][0]["conversion_factor"], 10)
		self.assertEqual(resolved["items"][0]["stock_qty"], 20)

	def test_valid_historical_rows_need_no_item_master_lookup(self):
		with patch.object(migration, "frappe") as framework:
			plan = migration.build_copy(source_fixture())
			self.assertFalse(plan["uom_recoveries"])
			framework.get_doc.assert_not_called()

	def test_missing_or_wrong_source_cannot_be_matched_by_item_code(self):
		for update in ({"name": "other-row"}, {"parent": "OTHER-SO"}, {"item_code": "OTHER"}):
			with self.subTest(update=update):
				sales = copy.deepcopy(self.sales)
				sales["items"][0].update(update)
				with self.assertRaises(migration.MigrationConflict):
					migration.build_copy(self.source, sales_order=sales)
		with self.assertRaises(migration.MigrationConflict):
			migration.build_copy(self.source)

	def test_wrong_parties_or_draft_source_block_recovery(self):
		for update in ({"company": "Other"}, {"customer": "Other"}, {"docstatus": 0}, {"name": "OTHER-SO"}):
			with self.subTest(update=update), self.assertRaises(migration.MigrationConflict):
				migration.build_copy(self.source, sales_order={**self.sales, **update})

	def test_cancelled_source_supplies_history_without_reactivation(self):
		self.sales["docstatus"] = 2
		plan = migration.build_copy(self.source, sales_order=self.sales)
		self.assertEqual(plan["items"][0]["stock_qty"], 20)
		self.assertEqual(self.sales["docstatus"], 2)

	def test_contradictory_values_are_not_overwritten(self):
		for update in (
			{"uom": "Pcs"},
			{"stock_uom": "Kg"},
			{"conversion_factor": 5},
			{"stock_qty": 99},
			{"stock_qty": -1},
		):
			with self.subTest(update=update):
				source = copy.deepcopy(self.source)
				source["items"][0].update(update)
				with self.assertRaises(migration.MigrationConflict):
					migration.build_copy(source, sales_order=self.sales)

	def test_nonfinite_invalid_or_insufficient_source_quantities_block(self):
		for update in (
			{"qty": 1, "stock_qty": 10},
			{"conversion_factor": 0},
			{"stock_qty": 29},
			{"conversion_factor": float("nan")},
			{"qty": float("inf")},
		):
			with self.subTest(update=update):
				sales = copy.deepcopy(self.sales)
				sales["items"][0].update(update)
				with self.assertRaises(migration.MigrationConflict):
					migration.build_copy(self.source, sales_order=sales)

	def test_duplicate_source_row_requires_review(self):
		self.source["items"].append({**self.source["items"][0], "name": "duplicate"})
		with self.assertRaisesRegex(migration.MigrationConflict, "duplicate Sales Order row"):
			migration.build_copy(self.source, sales_order=self.sales)

	def test_retry_uses_recovery_evidence_and_blocks_source_drift(self):
		plan = migration.build_copy(self.source, sales_order=self.sales)
		existing = {
			**copy.deepcopy(plan["demand"]),
			"items": copy.deepcopy(plan["items"]),
			"legacy_delivery_order_snapshot": json.dumps(
				{"source": self.source, "uom_recoveries": plan["uom_recoveries"]}
			),
		}
		self.assertEqual(migration.build_copy(self.source, existing, self.sales)["items"], plan["items"])
		self.sales["items"][0].update(qty=4, stock_qty=40)
		with self.assertRaisesRegex(migration.MigrationConflict, "evidence changed"):
			migration.build_copy(self.source, existing, self.sales)

	def test_normal_save_uses_verified_projection_without_source_write(self):
		plan = migration.build_copy(self.source, sales_order=self.sales)
		previous = SimpleNamespace(
			**plan["demand"],
			legacy_delivery_order_snapshot=json.dumps({"uom_recoveries": plan["uom_recoveries"]}),
		)
		values = copy.deepcopy(
			{"doctype": "Delivery Order", **self.source["document"], "items": self.source["items"]}
		)
		doc = SimpleNamespace(**values)
		doc.items = [frappe._dict(row) for row in values["items"]]
		doc.as_dict = lambda: copy.deepcopy(values)

		def recovered_document(values):
			projected = SimpleNamespace(**values)
			projected.items = [frappe._dict(row) for row in values["items"]]
			return projected

		with (
			patch.object(migration.frappe, "get_doc", side_effect=recovered_document),
			patch.object(migration, "_source", return_value=self.source),
			patch.object(migration, "_sales_order_for_recovery", return_value=self.sales),
			patch.object(migration.frappe.db, "set_value") as write,
		):
			projected = migration.prepared_source_for_validation(doc, previous)
			self.assertEqual(projected.items[0].conversion_factor, 10)
			self.assertEqual(doc.items[0].conversion_factor, 0)
			write.assert_not_called()
