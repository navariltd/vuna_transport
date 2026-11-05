import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.utils import flt

from vuna_transport.services.demand import depot_return_demand_service as service


class TestDepotReturnDemand(unittest.TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(service, "frappe"))
		self.framework.db.exists.return_value = False
		self.framework.db.get_value.return_value = "Stores"
		self.enterContext(patch.object(service, "_", side_effect=lambda text: text))
		self.framework.throw.side_effect = ValueError
		self.posted = self.enterContext(patch.object(service, "posted_pickup_quantities", return_value={}))
		self.unplanned = self.enterContext(
			patch(
				"vuna_transport.services.execution.unplanned_pickup_service.unplanned_collected_quantities",
				return_value={},
			)
		)
		self.enterContext(
			patch.object(
				service,
				"flt",
				side_effect=lambda value, precision=None: flt(
					value, precision, rounding_method="Banker's Rounding"
				),
			)
		)
		self.row = frappe._dict(
			name="RRI-1",
			item_code="MILK",
			custody_warehouse="DEPOT-QUARANTINE",
			requested_return_qty=4,
			conversion_factor=10,
			uom="Carton",
			stock_uom="Pcs",
		)
		self.request = SimpleNamespace(
			name="RR-1",
			docstatus=1,
			doctype="Return Request",
			return_context="Depot",
			company="Company",
			source_warehouse="DEPOT-QUARANTINE",
			return_warehouse="FACTORY-RETURNS",
			initiation_date="2026-11-04",
			items=[self.row],
		)

	def test_depot_returns_keep_requested_uom(self):
		leg = service.build_depot_return_legs(self.request)[0]
		self.assertEqual(leg["source_warehouse"], "DEPOT-QUARANTINE")
		self.assertEqual(leg["destination_warehouse"], "FACTORY-RETURNS")
		self.assertEqual(leg["items"][0]["transaction_qty"], 4)
		self.assertEqual(leg["items"][0]["stock_qty"], 40)
		self.assertEqual(leg["items"][0]["transaction_uom"], "Carton")

	def test_onboard_goods_never_become_another_pickup(self):
		self.framework.db.get_value.return_value = "Transit"
		self.assertEqual(service.build_depot_return_legs(self.request), [])

	def test_unplanned_collections_are_not_picked_up_again(self):
		self.unplanned.return_value = {"RRI-1": 20}
		item = service.build_depot_return_legs(self.request)[0]["items"][0]
		self.assertEqual((item["transaction_qty"], item["stock_qty"]), (2, 20))

	def test_only_uncollected_quantity_is_available(self):
		self.posted.return_value = {"RRI-1": 25}
		item = service.build_depot_return_legs(self.request)[0]["items"][0]
		self.assertEqual((item["transaction_qty"], item["stock_qty"]), (1.5, 15))

	def test_fully_collected_rows_are_not_planned_twice(self):
		self.posted.return_value = {"RRI-1": 40}
		self.assertEqual(service.build_depot_return_legs(self.request), [])

	def test_customer_returns_are_not_this_adapter(self):
		self.request.return_context = "Customer"
		with self.assertRaises(ValueError):
			service.build_depot_return_legs(self.request)

	def test_draft_returns_are_not_pickup_authorization(self):
		self.request.docstatus = 0
		with self.assertRaises(ValueError):
			service.build_depot_return_legs(self.request)

	def test_same_source_and_destination_are_rejected(self):
		self.request.return_warehouse = "DEPOT-QUARANTINE"
		with self.assertRaises(ValueError):
			service.build_depot_return_legs(self.request)
