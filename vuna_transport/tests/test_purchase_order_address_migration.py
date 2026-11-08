"""Remove the duplicate address without silently changing collection locations."""

from unittest import TestCase
from unittest.mock import patch

import frappe

from vuna_transport.patches import use_standard_purchase_order_addresses as migration


class TestPurchaseOrderAddressMigration(TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(migration, "frappe"))
		self.enterContext(patch.object(migration, "_", side_effect=lambda message: message))
		self.framework.db.get_value.return_value = "Purchase Order-vuna_pickup_address"
		self.framework.db.has_column.return_value = True
		self.framework.db.sql.return_value = [
			frappe._dict(name="PO-1", vuna_pickup_address="Gate", dispatch_address=None)
		]
		self.render = self.enterContext(
			patch.object(migration, "get_address_display", return_value="Gate address")
		)

		def fail(message):
			raise ValueError(message)

		self.framework.throw.side_effect = fail

	def test_legacy_pickup_moves_to_standard_field_without_business_hooks(self):
		migration.execute()
		self.framework.db.set_value.assert_called_once_with(
			"Purchase Order",
			"PO-1",
			{"dispatch_address": "Gate", "dispatch_address_display": "Gate address"},
			update_modified=False,
		)
		self.framework.delete_doc.assert_called_once_with(
			"Custom Field", "Purchase Order-vuna_pickup_address", ignore_permissions=True
		)
		self.framework.db.commit.assert_not_called()

	def test_existing_matching_dispatch_address_is_not_rewritten(self):
		self.framework.db.sql.return_value[0].dispatch_address = "Gate"
		migration.execute()
		self.framework.db.set_value.assert_not_called()
		self.framework.delete_doc.assert_called_once()

	def test_conflicting_standard_address_stops_without_deleting_or_overwriting(self):
		self.framework.db.sql.return_value[0].dispatch_address = "Different Gate"
		with self.assertRaisesRegex(ValueError, "PO-1"):
			migration.execute()
		self.framework.db.set_value.assert_not_called()
		self.framework.delete_doc.assert_not_called()

	def test_missing_address_record_stops_before_any_updates(self):
		self.render.side_effect = ValueError("Address missing")
		with self.assertRaises(ValueError):
			migration.execute()
		self.framework.db.set_value.assert_not_called()
		self.framework.delete_doc.assert_not_called()

	def test_removed_field_or_fresh_install_does_not_replay_old_column(self):
		self.framework.db.get_value.return_value = None
		migration.execute()
		self.framework.db.sql.assert_not_called()
		self.framework.db.set_value.assert_not_called()
		self.framework.delete_doc.assert_not_called()
