"""Purchase Receipt confirms collection independently of its target warehouse."""

from unittest import TestCase
from unittest.mock import patch

import frappe

from vuna_transport.services.execution import supplier_receipt_confirmation_service as service


class TestSupplierReceiptConfirmation(TestCase):
	def assignment(self, name="TASK", source="POI", qty=20, **overrides):
		return frappe._dict(
			dict(
				task_name=name,
				task_creation="2026-11-08 09:00:00",
				delivery_trip="TRIP",
				po_item=source,
				item_code="Milk",
				stock_uom="Pcs",
				stock_qty=qty,
			),
			**overrides,
		)

	def receipt(self, qty=20, **overrides):
		return frappe._dict(
			dict(
				receipt_name="PR",
				explicit_task=None,
				receipt_time="2026-11-08 10:00:00",
				receipt_user="receiver@example.test",
				po_item="POI",
				item_code="Milk",
				stock_uom="Pcs",
				stock_qty=qty,
			),
			**overrides,
		)

	def test_any_receiving_warehouse_can_confirm_full_collection(self):
		for warehouse in ("Main", "Vehicle Transit", "Depot"):
			state = service.allocate_receipts([self.assignment()], [self.receipt(warehouse=warehouse)], 6)[
				"TASK"
			]
			self.assertEqual(state["status"], "Completed")
			self.assertEqual(state["evidence"].receipt_name, "PR")

	def test_partial_receipts_accumulate_and_cancellation_reopens(self):
		rows = [self.receipt(8), self.receipt(12, receipt_name="PR2")]
		for receipts, expected in ((rows, "Completed"), (rows[:1], "Partially Received"), ([], "Ready")):
			self.assertEqual(
				service.allocate_receipts([self.assignment()], receipts, 6)["TASK"]["status"], expected
			)

	def test_every_po_row_must_match_not_just_total(self):
		items = [self.assignment(source="A", qty=10), self.assignment(source="B", qty=10)]
		state = service.allocate_receipts(items, [self.receipt(20, po_item="A")], 6)["TASK"]
		self.assertEqual(state["status"], "Partially Received")

	def test_same_receipt_is_not_counted_for_two_trips(self):
		items = [self.assignment("FIRST"), self.assignment("SECOND", delivery_trip="OTHER")]
		states = service.allocate_receipts(items, [self.receipt(25)], 6)
		self.assertEqual(states["FIRST"]["status"], "Ready")
		self.assertEqual(states["SECOND"]["status"], "Ready")

	def test_explicit_receipts_are_reserved_before_generic_receipts(self):
		items = [self.assignment("FIRST"), self.assignment("SECOND")]
		states = service.allocate_receipts(
			items, [self.receipt(20), self.receipt(20, explicit_task="FIRST")], 6
		)
		self.assertTrue(all(state["status"] == "Completed" for state in states.values()))

	def test_other_task_receipt_cannot_complete_current_task(self):
		state = service.allocate_receipts([self.assignment()], [self.receipt(explicit_task="OTHER")], 6)[
			"TASK"
		]
		self.assertEqual(state["status"], "Ready")

	def test_old_receipts_do_not_satisfy_new_assignments(self):
		state = service.allocate_receipts(
			[self.assignment()], [self.receipt(receipt_time="2026-11-07 10:00:00")], 6
		)["TASK"]
		self.assertEqual(state["status"], "Ready")

	def test_wrong_item_stock_uom_or_source_cannot_confirm(self):
		for override in ({"po_item": "OTHER"}, {"item_code": "OTHER"}, {"stock_uom": "Kg"}, {"stock_qty": 0}):
			state = service.allocate_receipts([self.assignment()], [self.receipt(**override)], 6)["TASK"]
			self.assertEqual(state["status"], "Ready")

	def test_stock_uom_precision_handles_transaction_conversion(self):
		state = service.allocate_receipts([self.assignment(qty=0.3)], [self.receipt(0.1 + 0.2)], 6)["TASK"]
		self.assertEqual(state["status"], "Completed")

	def test_audit_uses_latest_receipt_not_explicit_processing_order(self):
		state = service.allocate_receipts(
			[self.assignment()],
			[
				self.receipt(10, receipt_name="LATER", receipt_time="2026-11-08 12:00:00"),
				self.receipt(10, receipt_name="EARLIER", explicit_task="TASK"),
			],
			6,
		)["TASK"]
		self.assertEqual(state["evidence"].receipt_name, "LATER")

	def test_sync_recalculates_trip_and_clears_audit_after_cancel(self):
		item = self.assignment()
		with (
			patch.object(service, "frappe") as framework,
			patch.object(service, "assigned_items", return_value=[item]),
			patch.object(
				service,
				"order_progress",
				return_value={"TASK": {"status": "Ready", "received": {}, "evidence": None}},
			),
			patch("vuna_transport.services.execution.transfer_execution_service._lock_task"),
			patch("vuna_transport.services.execution.trip_task_service.sync_task_execution") as sync,
		):
			service.sync_receipt(frappe._dict(items=[frappe._dict(purchase_order="PO")]))
			self.assertIsNone(framework.db.set_value.call_args.args[2]["receipt_purchase_receipt"])
			sync.assert_called_once()
