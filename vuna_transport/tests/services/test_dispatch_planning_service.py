# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.exceptions import ValidationError
from frappe.tests import UnitTestCase
from frappe.utils import getdate

from vuna_transport.services.planning.dispatch_filter_service import validate_additional_filters
from vuna_transport.services.planning.dispatch_planning_service import (
	_delivery_date_filter,
	_filter_allocation_state,
	_planning_filters,
	_summarize_vehicle_categories,
	_warehouse_summaries,
)
from vuna_transport.services.planning.load_suggestion_service import _partition_orders


class TestDispatchPlanningService(UnitTestCase):
	def test_planning_filters_are_server_authoritative(self):
		filters = _planning_filters(
			"Vuna Foods",
			"2026-08-24",
			"2026-08-30",
			"Main Warehouse",
			"Nairobi East",
			"Company Delivery",
			"High",
			"Customer A",
		)
		self.assertEqual(filters["company"], "Vuna Foods")
		self.assertEqual(filters["docstatus"], 1)
		self.assertEqual(filters["status"][1], ("Open", "Partially Allocated"))
		self.assertEqual(filters["source_warehouse"], "Main Warehouse")
		self.assertEqual(
			filters["required_delivery_date"],
			["between", [getdate("2026-08-24"), getdate("2026-08-30")]],
		)

	def test_open_ended_delivery_date_filters(self):
		self.assertEqual(
			_delivery_date_filter("2026-08-24", None),
			[">=", getdate("2026-08-24")],
		)

	def test_location_filters_use_delivery_order_snapshots(self):
		filters = _planning_filters(
			"Vuna Foods",
			delivery_address="Address-1",
			territory="Meru",
			county="Meru County",
			town="Meru Town",
		)

		self.assertEqual(filters["delivery_address"], "Address-1")
		self.assertEqual(filters["territory"], "Meru")
		self.assertEqual(filters["county"], "Meru County")
		self.assertEqual(filters["town"], "Meru Town")

	def test_native_filter_group_accepts_custom_transport_demand_fields(self):
		with patch.dict(frappe.conf, {"customer_demand_cutover": 1}):
			filters = validate_additional_filters(
				[["Transport Demand", "transport_compatibility_group", "=", "Packed Dairy"]]
			)
		self.assertEqual(filters[0][1:], ["transport_compatibility_group", "=", "Packed Dairy"])

	def test_native_filter_group_cannot_override_security_fields(self):
		with self.assertRaises(ValidationError):
			validate_additional_filters([["Delivery Order", "company", "=", "Other Company"]])
		self.assertEqual(
			_delivery_date_filter(None, "2026-08-30"),
			["<=", getdate("2026-08-30")],
		)

	def test_invalid_delivery_date_range_is_rejected(self):
		with self.assertRaises(ValidationError):
			_delivery_date_filter("2026-08-30", "2026-08-24")

	def test_partial_allocation_filter_uses_live_balances(self):
		rows = [
			{"name": "DO-1", "allocated_quantity": 0, "reserved_quantity": 0, "unallocated_quantity": 10},
			{"name": "DO-2", "allocated_quantity": 5, "reserved_quantity": 0, "unallocated_quantity": 5},
			{"name": "DO-3", "allocated_quantity": 10, "reserved_quantity": 0, "unallocated_quantity": 0},
		]
		self.assertEqual(
			[row["name"] for row in _filter_allocation_state(rows, "Partially Allocated")],
			["DO-2"],
		)
		self.assertEqual(
			[row["name"] for row in _filter_allocation_state(rows, "Unallocated")],
			["DO-1"],
		)

	def test_warehouse_summary_aggregates_remaining_capacity(self):
		rows = [
			self._demand_row("Warehouse A", 8, weight=80, volume=8),
			self._demand_row("Warehouse A", 4, weight=40, volume=4),
			self._demand_row("Warehouse B", 3, weight=30, volume=3),
		]
		summaries = {row["source_warehouse"]: row for row in _warehouse_summaries(rows)}
		self.assertEqual(summaries["Warehouse A"]["delivery_order_count"], 2)
		self.assertEqual(summaries["Warehouse A"]["unallocated_quantity"], 12)
		self.assertEqual(summaries["Warehouse A"]["weight"], 120)

	def test_vehicle_category_summary_uses_canonical_states(self):
		rows = [
			self._availability("Available"),
			self._availability("Allocated"),
			self._availability("In Trip"),
			self._availability("Under Maintenance"),
			self._availability("Unavailable", compliance_status="Blocked"),
		]
		summary = _summarize_vehicle_categories(rows)[0]
		self.assertEqual(summary["total"], 5)
		self.assertEqual(summary["available"], 1)
		self.assertEqual(summary["allocated"], 1)
		self.assertEqual(summary["in_trip"], 1)
		self.assertEqual(summary["under_maintenance"], 1)
		self.assertEqual(summary["compliance_blocked"], 1)

	def test_suggestion_partition_allows_warehouse_and_date_variation(self):
		orders = [
			self._planning_order("DO-1", "Warehouse A", "2026-08-24"),
			self._planning_order("DO-2", "Warehouse B", "2026-08-25"),
		]
		pools = _partition_orders(orders)
		self.assertEqual(sum(len(pool) for pool in pools.values()), 2)
		self.assertEqual(len(pools), 1)

	@staticmethod
	def _demand_row(warehouse, remaining, **dimensions):
		return {
			"source_warehouse": warehouse,
			"unallocated_quantity": remaining,
			"weight": dimensions.get("weight", 0),
			"volume": dimensions.get("volume", 0),
			"pallets": dimensions.get("pallets", 0),
			"cases": dimensions.get("cases", 0),
			"crates": dimensions.get("crates", 0),
		}

	@staticmethod
	def _availability(state, compliance_status="Compliant"):
		return type(
			"Availability",
			(),
			{
				"vehicle_category": "8 Ton Truck",
				"operational_state": state,
				"compliance_status": compliance_status,
			},
		)()

	@staticmethod
	def _planning_order(name, warehouse, delivery_date):
		return type(
			"PlanningOrder",
			(),
			{
				"name": name,
				"company": "Vuna Foods",
				"delivery_method": "Company Delivery",
				"transport_compatibility_group": "Packed Dairy",
				"transport_zone": "Nairobi East",
				"refrigeration_required": False,
				"tail_lift_required": False,
				"required_body_type": None,
				"special_equipment_required": None,
				"source_warehouse": warehouse,
				"required_delivery_date": delivery_date,
			},
		)()
