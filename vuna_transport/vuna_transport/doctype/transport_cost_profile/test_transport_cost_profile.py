# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

from frappe.tests import UnitTestCase

from vuna_transport.vuna_transport.doctype.transport_cost_profile.transport_cost_profile import (
	_date_ranges_overlap,
)


class TestTransportCostProfile(UnitTestCase):
	def test_adjacent_validity_ranges_do_not_overlap(self):
		self.assertFalse(_date_ranges_overlap("2026-01-01", "2026-06-30", "2026-07-01", "2026-12-31"))

	def test_open_ended_validity_ranges_overlap(self):
		self.assertTrue(_date_ranges_overlap("2026-01-01", None, "2026-07-01", None))
