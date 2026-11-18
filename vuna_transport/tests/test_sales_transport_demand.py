import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from vuna_transport.services.demand import sales_transport_demand_service as service
from vuna_transport.services.planning import customer_demand_load_service as planning
from vuna_transport.vuna_transport.doctype.transport_load.transport_load import TransportLoad


class TestDirectSalesDemand(unittest.TestCase):
	def setUp(self):
		self.enterContext(patch.object(service, "item_requires_transport", return_value=True))

	def test_preview_switch_is_off_by_default_and_uses_numeric_site_opt_in(self):
		for value, expected in ((0, False), ("0", False), (1, True), ("1", True)):
			with (
				patch.dict(frappe.conf, {"customer_demand_cutover": value}),
				patch.object(frappe.db, "exists", return_value=True),
			):
				self.assertEqual(service.native_customer_demand_enabled(), expected)

	def test_group_key_preserves_group_boundaries_and_row_order_independence(self):
		key = ("2026-11-10", "Address", None, "Zone", None, None, "Company Delivery", "Milk")
		self.assertEqual(
			service.sales_source_key("SO-1", key, ["b", "a"]),
			service.sales_source_key("SO-1", key, ["a", "b"]),
		)
		self.assertNotEqual(
			service.sales_source_key("SO-1", key, ["a"]), service.sales_source_key("SO-1", key, ["b"])
		)
		self.assertNotEqual(
			service.sales_source_key("SO-1", key, ["a"]), service.sales_source_key("SO-2", key, ["a"])
		)

	@patch.object(service, "frappe")
	def test_native_and_legacy_rows_both_prevent_double_planning(self, framework):
		framework.db.sql_list.side_effect = [["native-row"], ["legacy-row"]]
		self.assertEqual(service.covered_source_items("SO-1"), {"native-row", "legacy-row"})
		self.assertEqual(framework.db.sql_list.call_count, 2)

	@patch.object(service, "get_warehouse_planning_data")
	@patch.object(service, "get_address_planning_data")
	@patch.object(service.sales, "build_sales_planning_snapshot")
	@patch.object(service.sales, "_get_group_key")
	@patch.object(service.sales, "_get_transport_quantity", return_value=2)
	@patch.object(service.sales, "_is_stock_item", side_effect=lambda row: row.item_code != "SERVICE")
	@patch.object(service.sales, "_requires_transport_planning", return_value=True)
	def test_direct_legs_preserve_sales_row_carton_uom_and_optional_origin(
		self, method, stock, qty, key, snapshot, address, origin
	):
		key.return_value = ("2026-11-10", "Address", None, None, None, None, "Company Delivery", None)
		address.return_value = frappe._dict(location=None, latitude=None, longitude=None)
		origin.return_value = frappe._dict(address=None, location=None, latitude=None, longitude=None)
		snapshot.return_value = {
			"sales_order": "SO-1",
			"items": [
				{
					"sales_order_item": "so-row",
					"item_code": "MILK",
					"ordered_qty": 3,
					"transport_qty": 2,
					"uom": "Carton",
					"conversion_factor": 10,
					"stock_qty": 20,
					"stock_uom": "Pcs",
				}
			],
		}
		source = SimpleNamespace(
			docstatus=1,
			status="To Deliver and Bill",
			name="SO-1",
			company="Company",
			customer="Customer",
			items=[
				SimpleNamespace(name="so-row", item_code="MILK", conversion_factor=10),
				SimpleNamespace(name="service-row", item_code="SERVICE", conversion_factor=1),
			],
		)
		leg = service.build_sales_legs(source)[0]
		self.assertEqual(
			(leg["source_document_type"], leg["source_document"], leg["demand_type"]),
			("Sales Order", "SO-1", "Selling"),
		)
		self.assertIsNone(leg["source_warehouse"])
		row = leg["items"][0]
		self.assertEqual((row["source_item"], row["sales_order_item"]), ("so-row", "so-row"))
		self.assertEqual(
			(row["transaction_qty"], row["transaction_uom"], row["stock_qty"]), (2, "Carton", 20)
		)
		self.assertEqual(len(leg["items"]), 1)

	def test_stopped_or_unsubmitted_source_cannot_generate(self):
		for state, status in ((0, "Draft"), (2, "Cancelled"), (1, "Closed"), (1, "On Hold")):
			with self.subTest(state=state, status=status), self.assertRaises(frappe.ValidationError):
				service.build_sales_legs(SimpleNamespace(docstatus=state, status=status))

	@patch.object(service.sales, "_requires_transport_planning", return_value=False)
	def test_counterparty_delivery_does_not_generate_demand(self, method):
		self.assertEqual(
			service.build_sales_legs(SimpleNamespace(docstatus=1, status="To Deliver and Bill")), []
		)


class TestCustomerDemandLoad(unittest.TestCase):
	def setUp(self):
		self.demand = frappe.get_doc(
			{
				"doctype": "Transport Demand",
				"name": "TD-1",
				"company": "Company",
				"docstatus": 1,
				"movement_type": "Customer Delivery",
				"source_document_type": "Sales Order",
				"source_document": "SO-1",
				"customer": "Customer",
				"sales_order": "SO-1",
				"destination_address": "Address",
				"required_date": "2026-11-10",
				"items": [
					{
						"name": "td-item",
						"item_code": "MILK",
						"item_name": "Milk",
						"transaction_qty": 2,
						"transaction_uom": "Carton",
						"conversion_factor": 10,
						"stock_qty": 20,
						"stock_uom": "Pcs",
						"weight": 20,
						"weight_uom": "Kg",
						"volume": 10,
						"volume_uom": "Litre",
					}
				],
			}
		)
		self.load = frappe.get_doc(
			{
				"doctype": "Transport Load",
				"company": "Company",
				"movement_type": "Customer Delivery",
				"delivery_orders": [
					{"transport_demand": "TD-1", "transport_demand_item": "td-item", "allocated_quantity": 1}
				],
			}
		)
		self.enterContext(patch.object(planning, "_read_demand", return_value=self.demand))
		self.enterContext(
			patch.object(
				planning,
				"available_items",
				return_value={
					"td-item": {
						"row": self.demand.items[0],
						"available_stock_qty": 20,
						"available_quantity": 2,
					}
				},
			)
		)
		self.enterContext(patch("vuna_transport.services.planning.demand_lifecycle_service.lock_demands"))

	def test_draft_customer_load_keeps_uom_dimensions_and_canonical_links(self):
		planning.validate_customer_allocations(self.load)
		row = self.load.delivery_orders[0]
		self.assertEqual((row.uom, row.allocated_quantity, row.allocated_stock_qty), ("Carton", 1, 10))
		self.assertEqual((row.weight, row.volume), (10, 5))
		self.assertEqual(
			(row.customer, row.delivery_address, row.sales_order), ("Customer", "Address", "SO-1")
		)
		self.assertFalse(row.delivery_order)
		self.assertIsNone(self.load.source_warehouse)
		self.assertEqual(self.load.total_stops, 1)

	def test_invalid_or_excess_quantities_are_rejected(self):
		for qty in (0, -1, 3, float("nan"), float("inf")):
			with self.subTest(qty=qty):
				self.load.delivery_orders[0].allocated_quantity = qty
				with self.assertRaises(frappe.ValidationError):
					planning.validate_customer_allocations(self.load)

	def test_wrong_parent_or_duplicate_item_is_rejected(self):
		self.load.delivery_orders[0].transport_demand_item = "other-item"
		with self.assertRaises(frappe.ValidationError):
			planning.validate_customer_allocations(self.load)
		self.load.delivery_orders[0].transport_demand_item = "td-item"
		self.load.append(
			"delivery_orders",
			{"transport_demand": "TD-1", "transport_demand_item": "td-item", "allocated_quantity": 1},
		)
		with self.assertRaises(frappe.ValidationError):
			planning.validate_customer_allocations(self.load)

	def test_wrong_company_is_rejected(self):
		self.load.company = "Other Company"
		with self.assertRaises(frappe.ValidationError):
			planning.validate_customer_allocations(self.load)

	def test_legacy_and_native_sales_rows_cannot_be_mixed(self):
		self.load.delivery_orders[0].delivery_order = "DO-1"
		with self.assertRaises(frappe.ValidationError):
			planning.validate_customer_allocations(self.load)

	def test_native_submission_uses_customer_validation_and_not_transfer_execution(self):
		self.load.source_warehouse = "Warehouse"
		with (
			patch(
				"vuna_transport.services.planning.customer_demand_load_service.validate_customer_allocations"
			) as validate,
			patch("vuna_transport.vuna_transport.doctype.transport_load.transport_load.validate_capacity"),
			patch(
				"vuna_transport.vuna_transport.doctype.transport_load.transport_load.recommend_vehicle_category",
				return_value="Canter",
			),
		):
			TransportLoad.before_submit(self.load)
			validate.assert_called_once_with(self.load)
		self.assertEqual(self.load.status, "Approved")
