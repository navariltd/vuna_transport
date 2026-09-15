# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

from frappe.tests import UnitTestCase

from vuna_transport.services.costing.transport_cost_estimation_service import _profile_is_active
from vuna_transport.vuna_transport.doctype.transport_cost_structure_assignment.transport_cost_structure_assignment import (
	_date_ranges_overlap,
)


class TestTransportCostStructureAssignment(UnitTestCase):
	def test_adjacent_validity_ranges_do_not_overlap(self):
		self.assertFalse(_date_ranges_overlap("2026-01-01", "2026-06-30", "2026-07-01", "2026-12-31"))

	def test_open_ended_validity_ranges_overlap(self):
		self.assertTrue(_date_ranges_overlap("2026-01-01", None, "2026-07-01", None))

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
