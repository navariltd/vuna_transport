"""Retired navigation cleanup is scoped, idempotent and preserves site intent."""

import unittest
from unittest.mock import patch

import frappe

from vuna_transport import install


class TestRetiredDeliveryOrderNavigation(unittest.TestCase):
	@patch.object(install, "frappe")
	def test_active_legacy_doctype_is_not_cleaned_up_early(self, framework):
		framework.db.exists.return_value = True
		self.assertEqual(install.cleanup_retired_delivery_order_navigation(), 0)
		framework.db.delete.assert_not_called()

	@patch.object(install, "frappe")
	def test_only_retired_links_in_this_apps_sidebars_are_removed(self, framework):
		framework.db.exists.side_effect = lambda dt, name: name == "Sidebar"
		framework.get_meta.return_value.get_field.return_value.options = "Sidebar Item"
		framework.get_all.side_effect = [
			["Dispatch Operations"],
			[
				frappe._dict(name="legacy", link_type="DocType", link_to="Delivery Order"),
				frappe._dict(name="old-report", link_type="Report", link_to="Unallocated Delivery Orders"),
				frappe._dict(name="demand", link_type="DocType", link_to="Transport Demand"),
				frappe._dict(name="custom", link_type="URL", link_to="Delivery Order"),
			],
		]
		self.assertEqual(install.cleanup_retired_delivery_order_navigation(), 2)
		self.assertEqual(framework.get_all.call_args_list[0].kwargs["filters"], {"app": "vuna_transport"})
		self.assertEqual(
			framework.get_all.call_args_list[1].kwargs["filters"]["parent"], ["in", ["Dispatch Operations"]]
		)
		framework.db.delete.assert_called_once_with(
			"Sidebar Item", {"name": ["in", ["legacy", "old-report"]]}
		)
		framework.clear_cache.assert_called_once_with()

	@patch.object(install, "frappe")
	def test_personal_customizations_are_scoped_to_transport_module(self, framework):
		framework.db.exists.side_effect = lambda dt, name: name == "Custom Sidebar"
		framework.get_meta.return_value.get_field.return_value.options = "Sidebar Item"
		framework.get_all.side_effect = [["Transport User Sidebar"], []]
		self.assertEqual(install.cleanup_retired_delivery_order_navigation(), 0)
		self.assertEqual(framework.get_all.call_args_list[0].kwargs["filters"], {"module": "Vuna Transport"})
		framework.db.delete.assert_not_called()
		framework.clear_cache.assert_not_called()

	@patch.object(install, "frappe")
	def test_empty_site_or_repeated_cleanup_is_a_noop(self, framework):
		framework.db.exists.side_effect = lambda dt, name: name != "Delivery Order"
		framework.get_all.return_value = []
		self.assertEqual(install.cleanup_retired_delivery_order_navigation(), 0)
		framework.db.delete.assert_not_called()
