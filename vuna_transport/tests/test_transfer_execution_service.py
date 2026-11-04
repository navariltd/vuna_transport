"""Dispatch manifest invariants without inventory side effects."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.utils import flt

from vuna_transport.services.execution import transfer_execution_service as service


class TestTransferDispatch(unittest.TestCase):
	def setUp(self):
		self.enterContext(
			patch.object(
				service,
				"flt",
				side_effect=lambda value, precision=None: flt(
					value, precision, rounding_method="Banker's Rounding"
				),
			)
		)
		self.framework = self.enterContext(patch.object(service, "frappe"))
		self.framework.throw.side_effect = ValueError
		self.framework.get_precision.return_value = 6
		self.enterContext(patch.object(service, "_", side_effect=lambda value: value))
		self.task = SimpleNamespace(
			items=[
				frappe._dict(
					name="TASK-ITEM",
					load_item="LOAD-ITEM",
					item_code="MILK",
					qty=2,
					uom="Carton",
					stock_uom="Pcs",
					conversion_factor=10,
					stock_qty=20,
				)
			]
		)
		self.demand = SimpleNamespace(
			source_document="MR-1",
			source_warehouse="MAIN",
			items=[frappe._dict(name="DEMAND-ITEM", source_item="MR-ITEM")],
		)
		self.load = frappe._dict(
			delivery_orders=[frappe._dict(name="LOAD-ITEM", transport_demand_item="DEMAND-ITEM")]
		)
		self.entry = SimpleNamespace(to_warehouse="TRANSIT", items=[self.row(2)])

	def row(self, qty):
		return frappe._dict(
			vuna_transport_task_item="TASK-ITEM",
			item_code="MILK",
			qty=qty,
			uom="Carton",
			stock_uom="Pcs",
			conversion_factor=10,
			transfer_qty=qty * 10,
			material_request="MR-1",
			material_request_item="MR-ITEM",
			s_warehouse="MAIN",
			t_warehouse="TRANSIT",
			precision=lambda field: 6,
		)

	def validate(self):
		service.validate_dispatch_items(self.entry, self.task, self.demand, self.load)

	def test_requested_uom_and_stock_quantity_are_preserved(self):
		self.validate()

	def test_batch_splits_can_preserve_one_planned_cargo_row(self):
		self.entry.items = [self.row(0.7), self.row(0.3), self.row(1)]
		self.validate()

	def test_incomplete_dispatch_requires_replanning(self):
		self.entry.items = [self.row(1)]
		with self.assertRaises(ValueError):
			self.validate()

	def test_extra_rows_and_changed_lineage_are_rejected(self):
		for field, value in (
			("vuna_transport_task_item", "OTHER"),
			("item_code", "OTHER"),
			("uom", "Pcs"),
			("conversion_factor", 1),
			("material_request_item", "OTHER"),
			("s_warehouse", "OTHER"),
			("t_warehouse", "OTHER"),
			("transfer_qty", 19),
		):
			with self.subTest(field=field):
				self.entry.items = [self.row(2)]
				self.entry.items[0][field] = value
				with self.assertRaises(ValueError):
					self.validate()

	def test_nonpositive_and_nonfinite_quantities_are_rejected(self):
		for qty in (0, -1, float("inf"), float("nan")):
			self.entry.items = [self.row(qty)]
			with self.assertRaises(ValueError):
				self.validate()

	def test_customer_only_trip_needs_no_transfer_dispatch(self):
		service.require_dispatch_stock_posted(frappe._dict(vuna_task_based=0))
		self.framework.get_all.assert_not_called()

	def test_unstarted_trip_cancellation_preserves_allocation_stock(self):
		self.framework.get_all.return_value = ["TASK-1"]
		self.framework.get_doc.return_value = frappe._dict(
			vuna_vehicle_allocation="VA-1", vuna_transport_load="TL-1"
		)
		with (
			patch.object(service, "_lock_task"),
			patch.object(service, "get_dispatch_entry", return_value="STE-1"),
		):
			service.guard_trip_stock_cancellation(
				frappe._dict(name="TRIP-1", vuna_task_based=1, vuna_actual_start=None)
			)
		self.framework.throw.assert_not_called()

	def test_started_trip_route_cancellation_does_not_reverse_allocation_stock(self):
		self.framework.get_all.return_value = ["TASK-1"]
		self.framework.get_doc.return_value = frappe._dict(
			vuna_vehicle_allocation="VA-1", vuna_transport_load="TL-1"
		)
		with (
			patch.object(service, "_lock_task"),
			patch.object(service, "get_dispatch_entry", return_value="STE-1"),
		):
			service.guard_trip_stock_cancellation(
				frappe._dict(name="TRIP-1", vuna_task_based=1, vuna_actual_start="2026-11-04")
			)
		self.framework.throw.assert_not_called()

	def test_legacy_trip_bound_stock_still_blocks_cancellation(self):
		self.framework.get_all.return_value = ["TASK-1"]
		self.framework.get_doc.return_value = frappe._dict(vuna_transport_task="TASK-1")
		with (
			patch.object(service, "_lock_task"),
			patch.object(service, "get_dispatch_entry", return_value="STE-1"),
			self.assertRaises(ValueError),
		):
			service.guard_trip_stock_cancellation(
				frappe._dict(name="TRIP-1", vuna_task_based=1, vuna_actual_start=None)
			)

	def test_posted_stock_does_not_reduce_remaining_demand_twice(self):
		from vuna_transport.services.planning.transport_load_service import available_stock_quantity

		self.assertEqual(available_stock_quantity(100, 60, 40, 40), 60)
		self.assertEqual(available_stock_quantity(100, 60, 50, 40), 50)
		self.assertEqual(available_stock_quantity(100, 50, 50, 40), 40)
		self.assertEqual(available_stock_quantity(100, 200, 40, 40), 60)

	def test_transfer_departure_requires_submitted_stock(self):
		self.framework.get_all.return_value = ["TASK-1"]
		with (
			patch.object(service, "_lock_task"),
			patch.object(service, "get_dispatch_entry", return_value=None),
		):
			with self.assertRaises(ValueError):
				service.require_dispatch_stock_posted(frappe._dict(name="TRIP-1", vuna_task_based=1))

	def test_dispatch_cannot_be_cancelled_after_departure(self):
		self.framework.get_doc.return_value = frappe._dict(vuna_actual_start="2026-11-04")
		with patch.object(service, "_lock_task", return_value=frappe._dict(delivery_trip="TRIP-1")):
			with self.assertRaises(ValueError):
				service.guard_dispatch_cancellation(frappe._dict(vuna_transport_task="TASK-1"))

	def test_existing_draft_is_reused(self):
		entry = MagicMock()
		self.framework.get_doc.return_value = entry
		with (
			patch.object(service, "require_doctype_permission"),
			patch.object(service, "require_document_permission"),
			patch.object(service, "_lock_task", return_value=frappe._dict(name="TASK-1")),
			patch.object(service, "_dispatch_context", return_value=(MagicMock(), MagicMock(), MagicMock())),
			patch.object(service, "get_dispatch_entry", return_value="STE-1"),
		):
			self.assertIs(service.make_dispatch_stock_entry("TASK-1", "TRANSIT"), entry)
