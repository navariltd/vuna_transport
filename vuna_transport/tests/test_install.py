"""Installation saves valid policies without relaxing ordinary settings validation."""

import copy
import unittest
from unittest.mock import patch

import frappe

from vuna_transport import install
from vuna_transport.patches import migrate_transport_demand_sources


class TestTransportSettingsBootstrap(unittest.TestCase):
	def settings(self, **values):
		return frappe.get_doc(
			{
				"doctype": "Transport Settings",
				"load_suggestion_option_count": 5,
				"auto_create_delivery_orders": 1,
				"auto_submit_delivery_orders": 0,
				"demand_sources": [],
				**values,
			}
		)

	def test_fresh_install_seeds_sources_before_scan_role_save(self):
		settings = self.settings()
		saved = []

		def validate_save(**kwargs):
			settings.validate()
			saved.append(copy.deepcopy(settings.as_dict()))

		with (
			patch.object(install, "ensure_item_transport_requirement"),
			patch.object(install, "ensure_transport_uoms"),
			patch.object(install, "normalize_transport_cost_natures"),
			patch.object(install, "ensure_delivery_methods"),
			patch.object(install.frappe, "get_single", return_value=settings),
			patch.object(install.frappe.db, "exists", return_value=True),
			patch.object(install.frappe.db, "commit"),
			patch.object(settings, "save", side_effect=validate_save),
		):
			install.after_install()
		self.assertEqual(len(saved), 2)
		self.assertTrue(all(snapshot["demand_sources"] for snapshot in saved))
		self.assertEqual(settings.allowed_loaded_scan_roles[0].role, "Transport Manager")
		self.assertEqual(settings.allowed_dispatched_scan_roles[0].role, "Transport Dispatcher")
		self.assertEqual(
			[row.source_document_type for row in settings.demand_sources], ["Sales Order", "Material Request"]
		)
		self.assertTrue(all(not row.auto_submit_demand for row in settings.demand_sources))

	def test_existing_custom_policies_are_not_modified_or_saved(self):
		settings = self.settings(
			demand_sources=[
				{
					"source_document_type": "Purchase Order",
					"enabled": 0,
					"creation_mode": "Manual",
					"auto_submit_demand": 0,
				}
			]
		)
		before = copy.deepcopy(settings.as_dict())
		with (
			patch.object(install.frappe, "get_single", return_value=settings),
			patch.object(settings, "save") as save,
		):
			install.ensure_transport_demand_sources()
			save.assert_not_called()
		self.assertEqual(settings.as_dict(), before)

	def test_legacy_sales_choices_are_preserved_and_retries_are_noops(self):
		for automatic in (0, 1):
			with self.subTest(automatic=automatic):
				settings = self.settings(auto_create_delivery_orders=automatic, auto_submit_delivery_orders=1)
				with (
					patch.object(install.frappe, "get_single", return_value=settings),
					patch.object(settings, "save") as save,
				):
					install.ensure_transport_demand_sources()
					install.ensure_transport_demand_sources()
					save.assert_called_once_with(ignore_permissions=True)
				self.assertEqual(
					settings.demand_sources[0].creation_mode, "Automatic" if automatic else "Manual"
				)
				self.assertEqual(settings.demand_sources[0].auto_submit_delivery_orders, 1)
				self.assertEqual(settings.demand_sources[1].creation_mode, "Manual")

	def test_ordinary_settings_validation_still_rejects_empty_sources(self):
		with self.assertRaises(frappe.ValidationError):
			self.settings().validate()

	def test_upgrade_patch_uses_the_same_initializer(self):
		with patch.object(migrate_transport_demand_sources, "ensure_transport_demand_sources") as initialize:
			migrate_transport_demand_sources.execute()
			initialize.assert_called_once_with()
