"""Managed upgrades must convert history before model sync, never skip blockers."""

from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from vuna_transport.services.demand import delivery_order_migration as preparation
from vuna_transport.services.demand import delivery_order_retirement as retirement


class TestRetirementUpgradePatch(TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(retirement, "frappe"))
		self.framework.flags.in_migrate = self.framework.flags.in_patch = True
		self.framework.db.exists.return_value = True
		self.authorize = self.enterContext(patch.object(preparation, "_authorize"))
		self.schema = self.enterContext(patch.object(retirement, "install_schema"))
		self.inspect = self.enterContext(
			patch.object(
				retirement,
				"inspect",
				return_value={
					"blockers": [],
					"projection_checksum": "approved-current-plan",
				},
			)
		)
		self.retire = self.enterContext(
			patch.object(retirement, "retire", return_value={"verified": True, "retired": True})
		)
		self.guard = self.enterContext(patch.object(retirement, "guard_upgrade"))
		self.verify = self.enterContext(
			patch.object(retirement, "verify", return_value={"verified": True, "retired": True})
		)

	def test_schema_preparation_conversion_and_guard_run_in_order(self):
		calls = Mock()
		for name, operation in (
			("schema", self.schema),
			("inspect", self.inspect),
			("retire", self.retire),
			("guard", self.guard),
		):
			calls.attach_mock(operation, name)
		result = retirement.migrate_before_model_sync()
		self.assertTrue(result["retired"])
		self.assertEqual([call[0] for call in calls.mock_calls], ["schema", "inspect", "retire", "guard"])
		self.retire.assert_called_once_with(dry_run=False, expected_checksum="approved-current-plan")
		self.framework.db.commit.assert_not_called()

	def test_readiness_blockers_stop_before_any_retirement(self):
		self.inspect.return_value["blockers"] = ["DO-1: unknown UOM conversion"]
		with self.assertRaisesRegex(preparation.MigrationConflict, "unknown UOM"):
			retirement.migrate_before_model_sync()
		self.retire.assert_not_called()
		self.guard.assert_not_called()

	def test_conversion_failure_propagates_to_patch_runner(self):
		self.retire.side_effect = preparation.MigrationConflict("Ledger verification failed")
		with self.assertRaisesRegex(preparation.MigrationConflict, "Ledger verification"):
			retirement.migrate_before_model_sync()
		self.guard.assert_not_called()
		self.framework.db.commit.assert_not_called()

	def test_schema_failure_stops_before_inspection_or_conversion(self):
		self.schema.side_effect = RuntimeError("Schema preparation failed")
		with self.assertRaisesRegex(RuntimeError, "Schema preparation"):
			retirement.migrate_before_model_sync()
		self.inspect.assert_not_called()
		self.retire.assert_not_called()

	def test_automatic_entry_requires_both_migration_and_patch_context(self):
		for migrating, patching in ((False, False), (True, False), (False, True)):
			self.framework.flags.in_migrate, self.framework.flags.in_patch = migrating, patching
			with (
				self.subTest(migrating=migrating, patching=patching),
				self.assertRaises(preparation.MigrationConflict),
			):
				retirement.migrate_before_model_sync()
		self.schema.assert_not_called()

	def test_already_retired_sites_are_verified_without_reconversion(self):
		self.framework.db.exists.return_value = False
		self.framework.db.get_single_value.return_value = '{"state":"retired"}'
		self.assertTrue(retirement.migrate_before_model_sync()["verified"])
		self.verify.assert_called_once()
		self.schema.assert_not_called()
		self.retire.assert_not_called()

	def test_fresh_sites_without_history_are_noop(self):
		self.framework.db.exists.return_value = False
		self.framework.db.get_single_value.return_value = None
		self.framework.db.table_exists.return_value = False
		self.assertEqual(retirement.migrate_before_model_sync(), {"retired": True, "source_count": 0})
		self.schema.assert_not_called()
		self.retire.assert_not_called()

	def test_missing_legacy_metadata_cannot_hide_existing_raw_history(self):
		self.framework.db.exists.return_value = False
		self.framework.db.get_single_value.return_value = None
		self.framework.db.table_exists.return_value = True
		self.framework.db.sql.return_value = [("DO-1",)]
		with self.assertRaisesRegex(preparation.MigrationConflict, "Restore its metadata"):
			retirement.migrate_before_model_sync()
		self.retire.assert_not_called()

	def test_patch_list_places_conversion_after_renames_and_before_final_guard(self):
		root = Path(__file__).resolve().parents[1]
		pre_sync = (root / "patches.txt").read_text().split("[post_model_sync]")[0]
		self.assertLess(
			pre_sync.index("rename_transport_load_doctypes"),
			pre_sync.index("retire_delivery_orders_before_sync"),
		)
		self.assertLess(
			pre_sync.index("retire_delivery_orders_before_sync"),
			pre_sync.index("guard_delivery_order_retirement"),
		)
		import vuna_transport.hooks as hooks

		self.assertNotIn(
			"vuna_transport.services.demand.delivery_order_retirement.guard_upgrade",
			getattr(hooks, "before_migrate", []),
		)


class TestMigrationAuthorization(TestCase):
	def test_only_managed_patch_context_can_use_deployment_authority(self):
		with patch.object(preparation, "frappe") as framework:
			framework.session.user = "Administrator"
			framework.conf.maintenance_mode = False
			for migrating, patching in ((False, False), (True, False), (False, True)):
				framework.flags.in_migrate, framework.flags.in_patch = migrating, patching
				with self.assertRaises(preparation.MigrationConflict):
					preparation._authorize(False, False, False)
			framework.flags.in_migrate = framework.flags.in_patch = True
			preparation._authorize(False, False, False)
			framework.session.user = "driver@example.com"
			framework.get_roles.return_value = ["Driver"]
			framework.PermissionError = frappe.PermissionError
			with self.assertRaises(frappe.PermissionError):
				preparation._authorize(False, False, False)
