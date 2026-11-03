import unittest
from unittest.mock import patch

import frappe

from vuna_transport.services.planning.demand_lifecycle_service import (
	allocation_status,
	guard_source_cancellation,
)


class TestDemandLifecycle(unittest.TestCase):
	def test_open_without_commitments(self):
		self.assertEqual(allocation_status([frappe._dict(stock_qty=10, allocated=0, reserved=0)]), "Open")

	def test_partial_reservation(self):
		self.assertEqual(
			allocation_status([frappe._dict(stock_qty=10, allocated=0, reserved=4)]), "Partially Allocated"
		)

	def test_fully_reserved(self):
		self.assertEqual(
			allocation_status([frappe._dict(stock_qty=10, allocated=0, reserved=10)]), "Reserved"
		)

	def test_submitted_allocations_take_precedence(self):
		self.assertEqual(
			allocation_status([frappe._dict(stock_qty=10, allocated=10, reserved=0)]), "Allocated"
		)

	def test_unlike_uoms_must_not_cancel_each_others_balances(self):
		items = [
			frappe._dict(stock_qty=1, allocated=1, reserved=0),
			frappe._dict(stock_qty=100, allocated=0, reserved=0),
		]
		self.assertEqual(allocation_status(items), "Partially Allocated")

	@patch("vuna_transport.services.planning.demand_lifecycle_service.frappe")
	@patch("vuna_transport.services.planning.demand_lifecycle_service._", side_effect=lambda value: value)
	def test_cancellation_requires_explicit_demand_cancellation(self, translate, framework):
		framework.get_all.return_value = ["TD-1"]
		framework.throw.side_effect = ValueError
		with self.assertRaises(ValueError):
			guard_source_cancellation(frappe._dict(doctype="Material Request", name="MR-1"))

	@patch("vuna_transport.services.planning.demand_lifecycle_service.frappe")
	def test_cancellation_without_demand_remains_allowed(self, framework):
		framework.get_all.return_value = []
		guard_source_cancellation(frappe._dict(doctype="Material Request", name="MR-1"))
		framework.throw.assert_not_called()
