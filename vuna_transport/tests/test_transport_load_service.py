"""Allocation tests with isolated database and permission boundaries."""

import unittest
from unittest.mock import MagicMock, patch

import frappe
from frappe.utils import flt

from vuna_transport.services.planning.transport_load_service import validate_demand_allocations


class TestTransportLoadAllocations(unittest.TestCase):
	def setUp(self):
		self.enterContext(
			patch(
				"vuna_transport.services.planning.transport_load_service.flt",
				side_effect=lambda value, precision=None: flt(
					value, precision, rounding_method="Banker's Rounding"
				),
			)
		)
		self.framework = self.enterContext(
			patch("vuna_transport.services.planning.transport_load_service.frappe")
		)
		self.enterContext(
			patch(
				"vuna_transport.services.planning.transport_load_service._", side_effect=lambda value: value
			)
		)
		self.framework.throw.side_effect = ValueError
		self.enterContext(
			patch("vuna_transport.services.planning.transport_load_service.require_company_access")
		)
		self.commitments = self.enterContext(
			patch(
				"vuna_transport.services.planning.transport_load_service.get_committed_quantities",
				return_value={},
			)
		)
		self.enterContext(
			patch(
				"vuna_transport.services.planning.transport_load_service.validate_transport_compatibility_groups"
			)
		)
		self.legs = self.enterContext(
			patch("vuna_transport.services.planning.transport_load_service.build_source_legs")
		)
		self.item = frappe._dict(
			name="TDI-1",
			source_item="MRI-1",
			item_code="MILK",
			stock_qty=100,
			stock_uom="Pcs",
			weight=50,
			weight_uom="Kg",
		)
		self.demand = MagicMock(
			docstatus=1,
			status="Open",
			company="Company",
			source_warehouse="Main",
			destination_warehouse="Depot",
			movement_type="Warehouse Transfer",
			source_key="leg",
			items=[self.item],
		)
		self.framework.get_doc.return_value = self.demand
		self.framework.get_cached_value.return_value = 0
		self.legs.return_value = [
			{"source_key": "leg", "items": [{"source_item": "MRI-1", "stock_qty": 100}]}
		]
		self.row = MagicMock(transport_demand="TD-1", transport_demand_item="TDI-1", allocated_quantity=40)
		self.row.get.side_effect = lambda key: getattr(self.row, key, None)
		self.row.set.side_effect = lambda key, value: setattr(self.row, key, value)
		self.row.precision.return_value = 3
		self.load = MagicMock(
			name="TL-1", company="Company", source_warehouse="Main", delivery_orders=[self.row]
		)

	def test_partial_allocation_scales_weight_in_stock_uom(self):
		validate_demand_allocations(self.load)
		self.assertEqual(self.row.weight, 20)
		self.assertEqual(self.load.total_weight, 20)
		self.assertEqual(self.row.stock_uom, "Pcs")
		self.assertIn("for update", self.framework.db.sql.call_args.args[0])

	def test_reservations_prevent_over_allocation(self):
		self.commitments.return_value = {"TDI-1": 70}
		with self.assertRaises(ValueError):
			validate_demand_allocations(self.load)

	def test_source_fulfilment_reduces_availability(self):
		self.legs.return_value[0]["items"][0]["stock_qty"] = 20
		with self.assertRaises(ValueError):
			validate_demand_allocations(self.load)

	def test_draft_demand_is_rejected(self):
		self.demand.docstatus = 0
		with self.assertRaises(ValueError):
			validate_demand_allocations(self.load)

	def test_company_mismatch_is_rejected(self):
		self.load.company = "Other Company"
		with self.assertRaises(ValueError):
			validate_demand_allocations(self.load)

	def test_nonfinite_quantity_is_rejected(self):
		self.row.allocated_quantity = float("nan")
		with self.assertRaises(ValueError):
			validate_demand_allocations(self.load)

	def test_duplicate_item_is_rejected(self):
		self.load.delivery_orders.append(self.row)
		with self.assertRaises(ValueError):
			validate_demand_allocations(self.load)
