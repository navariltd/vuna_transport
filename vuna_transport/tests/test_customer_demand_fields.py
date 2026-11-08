"""Customer cockpit field transfer without duplicate UOM/quantity authorities."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from vuna_transport.services.demand.customer_demand_fields import (
	ITEM_ALIASES,
	ITEM_FIELDS,
	ROOT_ALIASES,
	ROOT_FIELDS,
	enrich_customer_demand,
)
from vuna_transport.services.demand.transport_demand_service import preserve_source_row_names


class TestCustomerDemandFields(unittest.TestCase):
	def test_projection_transfers_sales_identity_routing_capability_and_item_dimensions(self):
		source = frappe._dict(
			{
				"doctype": "Delivery Order",
				"name": "DO-1",
				"sales_order": "SO-1",
				"preferred_route": "North",
				"priority": "High",
				"tail_lift_required": 1,
				"source_latitude": -1.2,
				"destination_longitude": 36.8,
				"total_volume": 12,
				"items": [
					{
						"name": "old-1",
						"item_code": "MILK",
						"sales_order_item": "so-item-1",
						"ordered_qty": 3,
						"transport_qty": 2,
						"uom": "Carton",
						"stock_qty": 20,
						"volume": 12,
						"volume_uom": "Litre",
						"cases": 2,
						"weight": 10,
						"weight_uom": "Kg",
					}
				],
			}
		)
		source = SimpleNamespace(**source)
		source.get = lambda key: getattr(source, key, None)
		source.items = [frappe._dict(row) for row in source.items]
		demand = frappe.get_doc(
			{
				"doctype": "Transport Demand",
				"source_document_type": "Delivery Order",
				"items": [
					{
						"name": "td-item-1",
						"source_item": "old-1",
						"transaction_qty": 2,
						"transaction_uom": "Carton",
						"stock_qty": 20,
					}
				],
			}
		)
		enrich_customer_demand(demand, source)
		self.assertEqual(demand.sales_order, "SO-1")
		self.assertEqual(
			(demand.preferred_route, demand.priority, demand.tail_lift_required), ("North", "High", 1)
		)
		self.assertEqual(demand.source_latitude, -1.2)
		row = demand.items[0]
		self.assertEqual((row.name, row.sales_order_item, row.ordered_qty), ("td-item-1", "so-item-1", 3))
		self.assertEqual((row.transaction_qty, row.transaction_uom, row.stock_qty), (2, "Carton", 20))
		self.assertEqual((row.weight, row.volume, row.cases), (10, 12, 2))
		self.assertEqual(source.items[0].transport_qty, 2)

	def test_schema_has_all_transferred_fields_without_duplicate_equivalent_columns(self):
		root = Path(frappe.get_app_path("vuna_transport", "vuna_transport", "doctype"))
		for name, fields, aliases in (
			("transport_demand", ROOT_FIELDS, ROOT_ALIASES),
			("transport_demand_item", ITEM_FIELDS, ITEM_ALIASES),
		):
			with self.subTest(doctype=name):
				meta = json.loads((root / name / f"{name}.json").read_text())
				by_name = {f["fieldname"]: f for f in meta["fields"]}
				self.assertTrue(set(fields).issubset(by_name))
				self.assertTrue(set(aliases.values()).issubset(by_name))
				self.assertFalse(set(aliases).intersection(by_name))
				self.assertEqual(set(meta["field_order"]), set(by_name))
				self.assertEqual(len(meta["field_order"]), len(by_name))
				for field in fields:
					self.assertFalse(
						by_name[field].get("reqd"),
						f"{field} must not become mandatory on internal/buying demand",
					)

	def test_existing_child_names_are_retained_when_source_rows_reorder(self):
		previous = frappe._dict(
			items=[
				frappe._dict(name="td-a", source_item="source-a"),
				frappe._dict(name="td-b", source_item="source-b"),
			]
		)
		rows = [
			{"source_item": "source-b", "transaction_qty": 2},
			{"source_item": "source-a", "transaction_qty": 1},
		]
		result = preserve_source_row_names(rows, previous)
		self.assertEqual([r["name"] for r in result], ["td-b", "td-a"])
		self.assertNotIn("name", rows[0])

	def test_new_source_rows_are_not_given_an_existing_child_identity(self):
		previous = frappe._dict(items=[frappe._dict(name="td-a", source_item="source-a")])
		self.assertNotIn("name", preserve_source_row_names([{"source_item": "source-b"}], previous)[0])

	def test_unpersisted_client_child_names_are_not_an_authority(self):
		self.assertNotIn(
			"name", preserve_source_row_names([{"source_item": "source-a", "name": "forged-child"}], None)[0]
		)

	def test_save_keeps_persisted_reservations_and_ignores_payload_counters(self):
		previous = frappe._dict(
			items=[
				frappe._dict(
					name="td-a", source_item="source-a", reserved_stock_qty=5, allocated_stock_qty=10
				)
			]
		)
		row = preserve_source_row_names(
			[{"source_item": "source-a", "stock_qty": 20, "reserved_stock_qty": 999}], previous
		)[0]
		self.assertEqual(
			(row["reserved_stock_qty"], row["allocated_stock_qty"], row["remaining_stock_qty"]), (5, 10, 5)
		)

	def test_ambiguous_existing_source_lineage_is_rejected(self):
		previous = frappe._dict(
			items=[
				frappe._dict(name="td-a", source_item="source-a"),
				frappe._dict(name="td-b", source_item="source-a"),
			]
		)
		with patch(
			"vuna_transport.services.demand.transport_demand_service.frappe.throw", side_effect=ValueError
		):
			with self.assertRaises(ValueError):
				preserve_source_row_names([], previous)
