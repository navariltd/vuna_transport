# Copyright (c) 2026, Vuna Transport contributors and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import UnitTestCase

from vuna_transport.vuna_transport.doctype.dispatch_planning_filter.dispatch_planning_filter import (
	ALLOWED_FILTER_KEYS,
)


class TestDispatchPlanningFilter(UnitTestCase):
	def test_allowed_filter_keys_are_controlled(self):
		self.assertIn("delivery_group", ALLOWED_FILTER_KEYS)
		self.assertNotIn("__raw_sql__", ALLOWED_FILTER_KEYS)

	def test_duplicate_active_filter_is_rejected(self):
		first = frappe.get_doc(
			{
				"doctype": "Dispatch Planning Filter",
				"filter_name": "Test Delivery Group",
				"field_key": "transport_handling_profile",
				"filter_label": "Transport Handling Profile",
			}
		)
		first.insert(ignore_permissions=True)
		self.addCleanup(lambda: frappe.delete_doc("Dispatch Planning Filter", first.name, force=True))
		second = frappe.get_doc(
			{
				"doctype": "Dispatch Planning Filter",
				"filter_name": "Test Delivery Group 2",
				"field_key": "transport_handling_profile",
				"filter_label": "Transport Handling Profile",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			second.insert(ignore_permissions=True)
