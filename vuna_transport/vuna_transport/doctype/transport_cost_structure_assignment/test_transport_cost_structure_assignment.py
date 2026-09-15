# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from vuna_transport.services.costing.transport_cost_estimation_service import _profile_is_active
from vuna_transport.vuna_transport.doctype.transport_cost_structure_assignment.transport_cost_structure_assignment import (
	_date_ranges_overlap,
)


class TestTransportCostStructureAssignment(UnitTestCase):
	def test_assignment_uses_assignment_name_for_autoname(self):
		meta = frappe.get_meta("Transport Cost Structure Assignment")
		self.assertEqual(meta.autoname, "field:assignment_name")
		self.assertIsNotNone(meta.get_field("assignment_name"))

	def test_adjacent_validity_ranges_do_not_overlap(self):
		self.assertFalse(_date_ranges_overlap("2026-01-01", "2026-06-30", "2026-07-01", "2026-12-31"))

	def test_open_ended_validity_ranges_overlap(self):
		self.assertTrue(_date_ranges_overlap("2026-01-01", None, "2026-07-01", None))

	def test_empty_assignment_copies_components_from_structure(self):
		structure = frappe._dict(
			components=[
				frappe._dict(
					cost_component="Fuel",
					calculation_basis="Per Fuel Quantity",
					cost_nature="Variable",
				)
			]
		)
		doc = frappe.new_doc("Transport Cost Structure Assignment")
		doc.cost_structure = "Mileage"
		doc.set("components", [])

		with patch(
			"vuna_transport.vuna_transport.doctype.transport_cost_structure_assignment.transport_cost_structure_assignment.frappe.get_doc",
			return_value=structure,
		):
			doc.before_validate()

		self.assertEqual(len(doc.components), 1)
		self.assertEqual(doc.components[0].cost_component, "Fuel")

	def test_draft_profile_is_not_active(self):
		profile = type(
			"Profile",
			(),
			{
				"company": "Yovuna Tech",
				"docstatus": 0,
				"disabled": 0,
				"valid_from": "2026-01-01",
				"valid_to": None,
			},
		)()

		self.assertFalse(_profile_is_active(profile, "Yovuna Tech", "2026-09-10"))
