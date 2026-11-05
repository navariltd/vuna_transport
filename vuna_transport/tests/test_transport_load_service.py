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
				"vuna_transport.services.planning.transport_load_service.get_posted_transfer_quantities",
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
			transaction_uom="Pcs",
			conversion_factor=1,
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
			source_document_type="Material Request",
			source_key="leg",
			items=[self.item],
		)
		self.framework.get_doc.side_effect = (
			lambda doctype, name: MagicMock(company="Company", is_group=0, disabled=0)
			if doctype == "Warehouse"
			else self.demand
		)
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

	def test_cartons_remain_cartons_while_stock_balance_uses_pieces(self):
		self.item.transaction_uom = "Carton"
		self.item.conversion_factor = 10
		self.row.allocated_quantity = 4
		validate_demand_allocations(self.load)
		self.assertEqual(self.row.allocated_quantity, 4)
		self.assertEqual(self.row.uom, "Carton")
		self.assertEqual(self.row.allocated_stock_qty, 40)
		self.assertEqual(self.row.weight, 20)

	def test_draft_load_can_reserve_without_a_source_warehouse(self):
		self.load.source_warehouse = None
		self.load.docstatus = 0
		validate_demand_allocations(self.load)
		self.assertEqual(self.row.allocated_stock_qty, 40)

	def test_submitted_load_requires_a_source_warehouse(self):
		self.load.source_warehouse = None
		self.load.docstatus = 1
		with self.assertRaises(ValueError):
			validate_demand_allocations(self.load)

	def test_material_request_can_be_fulfilled_from_another_loading_warehouse(self):
		self.load.source_warehouse = "Other"
		validate_demand_allocations(self.load)
		self.assertEqual(self.demand.source_warehouse, "Main")
		self.assertEqual(self.load.source_warehouse, "Other")
		self.assertEqual(self.row.allocated_stock_qty, 40)

	def test_return_pickup_source_must_match_custody(self):
		self.demand.source_document_type = "Return Request"
		self.load.source_warehouse = "Other"
		with self.assertRaises(ValueError):
			validate_demand_allocations(self.load)

	def test_planning_pool_includes_material_requests_from_other_preferred_origins(self):
		from vuna_transport.services.planning.transport_load_service import get_transfer_planning_items

		self.framework.get_list.return_value = []
		with patch("vuna_transport.services.planning.transport_load_service.require_doctype_permission"):
			get_transfer_planning_items("Company", source_warehouse="Production")
		self.assertIn(
			["source_document_type", "=", "Material Request"],
			self.framework.get_list.call_args.kwargs["or_filters"],
		)

	def test_loading_warehouse_cannot_equal_the_destination(self):
		self.load.source_warehouse = "Depot"
		with self.assertRaises(ValueError):
			validate_demand_allocations(self.load)

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


class TestLoadingWarehouseChangeGuard(unittest.TestCase):
	def setUp(self):
		from vuna_transport.vuna_transport.doctype.transport_load import transport_load

		self.validate = transport_load._validate_source_warehouse_change
		self.framework = self.enterContext(patch.object(transport_load, "frappe"))
		self.framework.throw.side_effect = ValueError
		self.enterContext(patch.object(transport_load, "_", side_effect=lambda value: value))
		self.framework.db.exists.return_value = False
		self.load = MagicMock(docstatus=0, source_warehouse="Production", pick_list=None, delivery_trip=None)
		self.load.name = "TL-1"
		self.load.get_doc_before_save.return_value = MagicMock(source_warehouse="Preferred Main")

	def test_draft_source_can_change_before_execution(self):
		self.validate(self.load)

	def test_submitted_source_can_change_before_execution(self):
		self.load.docstatus = 1
		self.validate(self.load)

	def test_existing_dispatch_stock_blocks_source_change(self):
		for status in (0, 1):
			with self.subTest(docstatus=status):
				self.load.docstatus = status
				self.framework.db.exists.side_effect = lambda doctype, filters: doctype == "Stock Entry"
				with self.assertRaises(ValueError):
					self.validate(self.load)

	def test_existing_trip_blocks_source_change(self):
		self.load.delivery_trip = "TRIP-1"
		with self.assertRaises(ValueError):
			self.validate(self.load)
