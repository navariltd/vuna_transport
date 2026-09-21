# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import UnitTestCase


class TestTransportCostStructure(UnitTestCase):
	def test_model_controller_exists(self):
		self.assertFalse(frappe.get_meta("Transport Cost Structure").istable)

	def test_cost_structure_is_submittable(self):
		self.assertTrue(frappe.get_meta("Transport Cost Structure").is_submittable)

	def test_cost_nature_supports_fixed_and_variable_only(self):
		for doctype, fieldname in (
			("Transport Cost Component", "default_cost_nature"),
			("Transport Cost Structure Component", "cost_nature"),
			("Transport Cost Structure Assignment Component", "cost_nature"),
		):
			field = frappe.get_meta(doctype).get_field(fieldname)
			self.assertEqual(field.options.splitlines(), ["Fixed", "Variable"])

	def test_assignment_component_uses_clear_amount_labels(self):
		meta = frappe.get_meta("Transport Cost Structure Assignment Component")
		self.assertEqual(meta.get_field("rate_uom").label, "UOM")
		self.assertEqual(meta.get_field("flat_amount").label, "Amount")
		self.assertIsNone(meta.get_field("estimated_default_amount"))

	def test_cost_nature_is_read_only_in_structure_rows(self):
		for doctype in (
			"Transport Cost Structure Component",
			"Transport Cost Structure Assignment Component",
		):
			self.assertTrue(frappe.get_meta(doctype).get_field("cost_nature").read_only)
