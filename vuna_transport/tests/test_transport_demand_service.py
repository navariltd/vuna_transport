"""Source normalization tests without inventory or accounting writes."""

import unittest
from unittest.mock import patch

import frappe

from vuna_transport.services.demand.transport_demand_service import (
	build_source_legs,
	create_transport_demands,
	get_demand_type,
)


def transfer_source():
	return frappe._dict(
		doctype="Material Request",
		name="MR-1",
		docstatus=1,
		status="Pending",
		company="Company",
		material_request_type="Material Transfer",
		schedule_date="2026-11-03",
		items=[
			frappe._dict(
				name="row-1",
				idx=1,
				item_code="MILK",
				from_warehouse="Main",
				warehouse="Depot A",
				qty=10,
				stock_qty=100,
				ordered_qty=20,
				uom="Carton",
				stock_uom="Pcs",
				conversion_factor=10,
			)
		],
	)


class TestTransportDemandService(unittest.TestCase):
	def test_demand_type_uses_erpnext_terms_not_arranger_or_vehicle_type(self):
		self.assertEqual(get_demand_type("Customer Delivery"), "Selling")
		self.assertEqual(get_demand_type("Supplier Pickup"), "Buying")
		self.assertEqual(get_demand_type("Warehouse Transfer"), "Internal")

	def setUp(self):
		translation = patch(
			"vuna_transport.services.demand.transport_demand_service._", side_effect=lambda text: text
		)
		translation.start()
		self.addCleanup(translation.stop)

	def test_transfer_uses_outstanding_stock_quantity(self):
		leg = build_source_legs(transfer_source())[0]
		self.assertEqual(leg["items"][0]["stock_qty"], 80)
		self.assertEqual(leg["items"][0]["transaction_qty"], 8)
		self.assertEqual(leg["destination_warehouse"], "Depot A")
		self.assertIsNone(leg["customer"])

	def test_request_can_defer_source_selection_to_transport(self):
		source = transfer_source()
		source["items"][0].from_warehouse = None
		leg = build_source_legs(source)[0]
		self.assertIsNone(leg["source_warehouse"])
		self.assertEqual(leg["destination_warehouse"], "Depot A")
		self.assertEqual(leg["items"][0]["transaction_qty"], 8)
		self.assertEqual(leg["items"][0]["transaction_uom"], "Carton")

	def test_different_warehouse_pairs_produce_separate_legs(self):
		source = transfer_source()
		row = frappe._dict(source["items"][0])
		row.name = "row-2"
		row.warehouse = "Depot B"
		source["items"].append(row)
		legs = build_source_legs(source)
		self.assertEqual(len(legs), 2)
		self.assertNotEqual(legs[0]["source_key"], legs[1]["source_key"])

	def test_fully_fulfilled_rows_are_excluded(self):
		source = transfer_source()
		source["items"][0].ordered_qty = 100
		self.assertEqual(build_source_legs(source), [])

	def test_source_keys_are_stable(self):
		self.assertEqual(build_source_legs(transfer_source()), build_source_legs(transfer_source()))

	@patch("vuna_transport.services.demand.transport_demand_service.frappe.throw", side_effect=ValueError)
	def test_same_source_and_destination_are_rejected(self, throw):
		source = transfer_source()
		source["items"][0].warehouse = "Main"
		with self.assertRaises(ValueError):
			build_source_legs(source)

	@patch("vuna_transport.services.demand.transport_demand_service.require_doctype_permission")
	@patch("vuna_transport.services.demand.transport_demand_service.require_company_access")
	@patch("vuna_transport.services.demand.transport_demand_service.frappe")
	def test_retry_returns_existing_demand_without_insert(self, framework, company, permission):
		from unittest.mock import MagicMock

		source = MagicMock()
		values = transfer_source()
		for key, value in values.items():
			setattr(source, key, value)
		source.get.side_effect = values.get
		existing = MagicMock(docstatus=0)
		framework.as_json.side_effect = frappe.as_json
		framework.get_doc.side_effect = [source, existing]
		framework.db.get_value.return_value = "TD-1"
		with patch("vuna_transport.services.demand.source_policy_service.require_source_enabled"):
			self.assertEqual(create_transport_demands("Material Request", "MR-1"), ["TD-1"])
		existing.check_permission.assert_called_once_with("read")
		existing.insert.assert_not_called()

	def test_delivery_order_preserves_sales_lineage(self):
		source = transfer_source()
		source.doctype = "Delivery Order"
		source.customer = "Customer"
		source.delivery_address = "Customer Address"
		source.source_warehouse = "Main"
		source.required_delivery_date = "2026-11-04"
		source.transport_zone = "North"
		source["items"][0].transport_qty = 3
		leg = build_source_legs(source)[0]
		self.assertEqual(leg["items"][0]["stock_qty"], 30)
		self.assertEqual(leg["customer"], "Customer")
		self.assertEqual(leg["destination_address"], "Customer Address")

	@patch("vuna_transport.services.demand.transport_demand_service.frappe.throw", side_effect=ValueError)
	def test_invalid_sources_and_conversion_are_rejected(self, throw):
		for changes in ({"docstatus": 0}, {"material_request_type": "Purchase"}, {"status": "Stopped"}):
			source = transfer_source()
			source.update(changes)
			with self.assertRaises(ValueError):
				build_source_legs(source)
		source = transfer_source()
		source["items"][0].conversion_factor = 0
		with self.assertRaises(ValueError):
			build_source_legs(source)

	@patch(
		"vuna_transport.services.demand.transport_demand_service.require_doctype_permission",
		side_effect=PermissionError,
	)
	def test_generation_checks_create_permission_first(self, permission):
		with self.assertRaises(PermissionError):
			create_transport_demands("Material Request", "MR-1")
