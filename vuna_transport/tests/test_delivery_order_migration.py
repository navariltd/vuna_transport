"""Migration preflight and transaction safety; no stock/accounting replay."""

import copy
import unittest
from datetime import timedelta
from unittest.mock import MagicMock, patch

import frappe

from vuna_transport.services.demand import delivery_order_migration as migration


def source_fixture():
	return {
		"document": {
			"name": "DO-OLD-1",
			"company": "Dairy",
			"docstatus": 1,
			"status": "Dispatched",
			"customer": "Customer",
			"source_warehouse": "Main",
			"delivery_address": "Address",
			"owner": "original@example.com",
			"creation": "2026-01-01 08:00:00",
			"modified": "2026-01-02 08:00:00",
			"modified_by": "planner@example.com",
			"sales_order": "SO-1",
			"preferred_route": "North",
			"priority": "High",
			"tail_lift_required": 1,
			"custom_staging_field": "keep me",
		},
		"items": [
			{
				"name": "old-row-1",
				"parent": "DO-OLD-1",
				"parenttype": "Delivery Order",
				"idx": 1,
				"item_code": "MILK",
				"transport_qty": 2,
				"uom": "Carton",
				"conversion_factor": 10,
				"stock_qty": 20,
				"stock_uom": "Pcs",
				"s_warehouse": "Main",
				"sales_order_item": "so-row-1",
				"ordered_qty": 3,
				"volume": 12,
				"volume_uom": "Litre",
				"allocated_quantity": 1,
				"reserved_quantity": 0.5,
			}
		],
	}


def existing_fixture(source=None):
	plan = migration.build_copy(source or source_fixture())
	demand = copy.deepcopy(plan["demand"])
	demand["name"] = "TD-EXISTING"
	demand["items"] = copy.deepcopy(plan["items"])
	demand["items"][0].update(name="existing-row", parent="TD-EXISTING")
	return demand


class TestDeliveryOrderMigrationProjection(unittest.TestCase):
	def test_keeps_requested_uom_original_ids_metadata_and_complete_snapshot(self):
		source = source_fixture()
		original = copy.deepcopy(source)
		plan = migration.build_copy(source)
		self.assertEqual(source, original)
		self.assertEqual(plan["source"]["document"]["custom_staging_field"], "keep me")
		self.assertEqual(plan["source"]["items"][0]["sales_order_item"], "so-row-1")
		self.assertEqual(plan["demand"]["name"], "DO-OLD-1")
		self.assertEqual(plan["demand"]["owner"], "original@example.com")
		self.assertEqual(plan["demand"]["source_document_type"], "Delivery Order")
		self.assertEqual(plan["demand"]["sales_order"], "SO-1")
		self.assertEqual(plan["demand"]["preferred_route"], "North")
		self.assertEqual(plan["demand"]["status"], "Dispatched")
		row = plan["items"][0]
		self.assertEqual((row["sales_order_item"], row["ordered_qty"], row["volume"]), ("so-row-1", 3, 12))
		self.assertEqual((row["name"], row["source_item"]), ("old-row-1", "old-row-1"))
		self.assertEqual(
			(row["transaction_qty"], row["transaction_uom"], row["stock_qty"]), (2, "Carton", 20)
		)
		self.assertEqual(
			(row["reserved_stock_qty"], row["allocated_stock_qty"], row["remaining_stock_qty"]), (5, 10, 5)
		)

	def test_reuses_existing_demand_and_child_ids(self):
		plan = migration.build_copy(source_fixture(), existing_fixture())
		self.assertTrue(plan["existing"])
		self.assertEqual(plan["demand"]["name"], "TD-EXISTING")
		self.assertEqual(plan["items"][0]["name"], "existing-row")
		self.assertEqual(plan["items"][0]["parent"], "TD-EXISTING")

	def test_repeated_copy_is_deterministic(self):
		self.assertEqual(migration.build_copy(source_fixture()), migration.build_copy(source_fixture()))
		self.assertEqual(
			migration.build_copy(source_fixture(), existing_fixture()),
			migration.build_copy(source_fixture(), existing_fixture()),
		)

	def test_draft_and_cancelled_history_is_not_resubmitted(self):
		for state, status in ((0, "Draft"), (2, "Cancelled")):
			with self.subTest(state=state):
				source = source_fixture()
				source["document"]["docstatus"] = state
				plan = migration.build_copy(source)
				self.assertEqual(plan["demand"]["docstatus"], state)
				self.assertEqual(plan["demand"]["status"], status)
				self.assertEqual(plan["items"][0]["docstatus"], state)

	def test_source_closed_or_zero_outstanding_still_preserves_history(self):
		source = source_fixture()
		source["document"]["status"] = "Closed"
		source["items"][0].update(transport_qty=0, stock_qty=0, reserved_quantity=0, allocated_quantity=0)
		self.assertEqual(len(migration.build_copy(source)["items"]), 1)

	def test_invalid_uom_data_blocks_instead_of_guessing_conversion(self):
		for update in (
			{"conversion_factor": 0},
			{"uom": None},
			{"stock_qty": 21},
			{"transport_qty": -1},
			{"transport_qty": float("nan")},
		):
			with self.subTest(update=update):
				source = source_fixture()
				source["items"][0].update(update)
				with self.assertRaises(migration.MigrationConflict):
					migration.build_copy(source)

	def test_float_roundoff_is_not_treated_as_stock_corruption(self):
		source = source_fixture()
		source["items"][0].update(transport_qty=0.1, conversion_factor=3, stock_qty=0.3)
		self.assertEqual(migration.build_copy(source)["items"][0]["stock_qty"], 0.3)

	def test_conflicting_existing_quantities_are_not_overwritten(self):
		existing = existing_fixture()
		existing["items"][0]["transaction_qty"] = 3
		with self.assertRaisesRegex(migration.MigrationConflict, "transaction_qty differs"):
			migration.build_copy(source_fixture(), existing)

	def test_source_changes_after_preparation_require_review(self):
		source = source_fixture()
		existing = existing_fixture(source)
		source["document"]["custom_staging_field"] = "changed"
		with self.assertRaisesRegex(migration.MigrationConflict, "source changed"):
			migration.build_copy(source, existing)

	def test_conflicting_customer_planning_values_are_not_silently_overwritten(self):
		existing = existing_fixture()
		existing["preferred_route"] = "South"
		with self.assertRaisesRegex(migration.MigrationConflict, "preferred_route differs"):
			migration.build_copy(source_fixture(), existing)

	def test_customer_quantity_comparison_survives_database_float_coercion(self):
		existing = existing_fixture()
		existing["items"][0]["ordered_qty"] = 3.0
		migration.build_copy(source_fixture(), existing)

	def test_delivery_window_comparison_supports_native_time_storage(self):
		self.assertTrue(migration._customer_value_equal("delivery_from_time", "08:00:00", timedelta(hours=8)))
		self.assertTrue(migration._customer_value_equal("delivery_from_time", "00:00:00", timedelta(0)))
		self.assertFalse(migration._customer_value_equal("delivery_from_time", "08:00:00", None))

	def test_existing_child_row_set_must_be_exact(self):
		existing = existing_fixture()
		existing["items"][0]["source_item"] = "unrelated"
		with self.assertRaisesRegex(migration.MigrationConflict, "different row sets"):
			migration.build_copy(source_fixture(), existing)

	def test_source_and_target_cancelled_states_must_agree(self):
		existing = existing_fixture()
		existing["docstatus"] = 2
		with self.assertRaisesRegex(migration.MigrationConflict, "cancelled"):
			migration.build_copy(source_fixture(), existing)

	def test_mixed_source_warehouses_are_not_collapsed(self):
		source = source_fixture()
		source["items"][0]["s_warehouse"] = "Other"
		with self.assertRaisesRegex(migration.MigrationConflict, "multiple loading warehouses"):
			migration.build_copy(source)

	def test_wrong_company_or_source_is_rejected(self):
		for update in (
			{"company": "Other"},
			{"source_document": "DO-OTHER"},
			{"legacy_delivery_order": "DO-OTHER"},
		):
			with self.subTest(update=update):
				existing = existing_fixture()
				existing.update(update)
				with self.assertRaises(migration.MigrationConflict):
					migration.build_copy(source_fixture(), existing)


class TestDeliveryOrderMigrationSafety(unittest.TestCase):
	def test_apply_requires_backup_maintenance_and_paused_workers(self):
		with patch.object(migration, "frappe") as framework:
			framework.session.user = "Administrator"
			for maintenance, backup, workers in (
				(False, True, True),
				(True, False, True),
				(True, True, False),
			):
				framework.conf.maintenance_mode = maintenance
				with self.assertRaises(migration.MigrationConflict):
					migration._authorize(False, backup, workers)
			framework.conf.maintenance_mode = True
			migration._authorize(False, True, True)

	def test_driver_cannot_run_admin_migration(self):
		with patch.object(migration, "frappe") as framework:
			framework.session.user = "driver@example.com"
			framework.get_roles.return_value = ["Driver"]
			framework.PermissionError = frappe.PermissionError
			with self.assertRaises(frappe.PermissionError):
				migration._authorize(True, False, False)

	def test_string_false_is_not_a_boolean(self):
		with patch.object(migration, "frappe") as framework:
			framework.session.user = "Administrator"
			with self.assertRaises(ValueError):
				migration._authorize("False", False, False)

	@patch.object(migration, "_authorize")
	@patch.object(migration, "_schema_errors", return_value=[])
	@patch.object(migration, "_plans", return_value=[])
	@patch.object(migration, "_link_changes", return_value=[])
	@patch.object(migration, "_lock_sources")
	@patch.object(migration, "_fingerprint")
	@patch.object(migration, "_write_plan")
	def test_default_dry_run_never_locks_or_writes(
		self, write, fingerprint, lock, changes, plans, schema, authorize
	):
		with patch.object(migration.frappe.db, "exists", return_value=True):
			report = migration.prepare(source_names=["DO-1"])
		self.assertEqual(report["mode"], "dry_run")
		self.assertFalse(report["ready_for_retirement"])
		self.assertFalse(report["whole_site_scope"])
		for action in (write, fingerprint, lock):
			action.assert_not_called()

	def test_existing_link_conflicts_are_reported_not_overwritten(self):
		plan = migration.build_copy(source_fixture())
		with patch.object(migration, "frappe") as framework:
			framework.get_all.return_value = [
				frappe._dict(
					name="load-row",
					delivery_order="DO-OLD-1",
					delivery_order_item="old-row-1",
					transport_demand="OTHER",
				)
			]
			with patch.object(
				migration, "LINKS", (("Transport Load Item", "delivery_order", "transport_demand", False),)
			):
				errors = []
				self.assertEqual(migration._link_changes([plan], errors), [])
				self.assertIn("already points to OTHER", errors[0])
				framework.db.set_value.assert_not_called()

	def test_apply_rolls_back_on_ledger_change_and_never_commits(self):
		plan = migration.build_copy(source_fixture())
		with (
			patch.object(migration, "frappe") as framework,
			patch.object(migration, "_authorize"),
			patch.object(migration, "_schema_errors", return_value=[]),
			patch.object(migration, "_plans", return_value=[plan]),
			patch.object(migration, "_link_changes", return_value=[]),
			patch.object(migration, "_lock_sources"),
			patch.object(migration, "_write_plan"),
			patch.object(migration, "_verify"),
			patch.object(migration, "_fingerprint", side_effect=["before"] * 4 + ["changed"] * 4),
		):
			with self.assertRaisesRegex(migration.MigrationConflict, "rolled back"):
				migration.prepare(
					dry_run=False, backup_confirmed=True, workers_paused=True, source_names=["DO-OLD-1"]
				)
			framework.db.rollback.assert_called_once_with(save_point="delivery_order_preparation")
			framework.db.commit.assert_not_called()

	def test_existing_demand_is_not_resaved_or_resubmitted(self):
		plan = migration.build_copy(source_fixture(), existing_fixture())
		with (
			patch.object(migration, "frappe") as framework,
			patch.object(migration, "_audit", return_value={}),
		):
			migration._write_plan(plan)
			framework.get_doc.assert_not_called()
			self.assertEqual(framework.db.set_value.call_count, 3)

	def test_new_demand_uses_historical_db_insert_not_business_insert(self):
		plan = migration.build_copy(source_fixture())
		with (
			patch.object(migration, "frappe") as framework,
			patch.object(migration, "_audit", return_value={}),
		):
			document = MagicMock()
			framework.get_doc.return_value = document
			migration._write_plan(plan)
			self.assertEqual(document.db_insert.call_count, 2)
			document.insert.assert_not_called()
			document.submit.assert_not_called()
