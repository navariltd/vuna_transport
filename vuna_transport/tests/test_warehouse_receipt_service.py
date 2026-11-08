"""Receiving ERPNext stock can confirm depot tasks, never customer deliveries."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from vuna_transport.services.execution import warehouse_receipt_service as service


class TestWarehouseReceipt(unittest.TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(service, "frappe"))

		def reject(message):
			raise ValueError(message)

		self.framework.throw.side_effect = reject
		self.enterContext(patch.object(service, "_", side_effect=lambda message: message))
		self.framework.get_precision.return_value = 6
		self.framework.db.exists.return_value = False
		self.task = SimpleNamespace(
			name="TASK",
			task_type="Transfer Receipt",
			transport_demand="TD",
			company="Company",
			delivery_trip="TRIP",
			items=[frappe._dict(load_item="LI", transport_demand_item="DI", stock_qty=20)],
		)
		self.demand = SimpleNamespace(
			source_document_type="Material Request",
			movement_type="Warehouse Transfer",
			source_document="MR",
			destination_warehouse="Depot",
			items=[frappe._dict(name="DI", source_item="MRI")],
		)
		self.source = frappe._dict(
			name="SED",
			vuna_transport_load_item="LI",
			material_request="MR",
			material_request_item="MRI",
			item_code="Milk",
			qty=2,
			uom="Carton",
			conversion_factor=10,
			stock_uom="Pcs",
			transfer_qty=20,
			batch_no="B1",
			serial_no="",
			serial_and_batch_bundle=None,
		)
		self.stock = SimpleNamespace(
			name="OUT",
			docstatus=1,
			company="Company",
			add_to_transit=1,
			to_warehouse="Transit",
			items=[self.source],
		)
		self.framework.get_doc.side_effect = (
			lambda dt, name: self.stock if dt == "Stock Entry" else self.demand
		)
		self.enterContext(
			patch(
				"vuna_transport.services.execution.transfer_execution_service.get_dispatch_entry",
				return_value="OUT",
			)
		)
		self.enterContext(
			patch(
				"vuna_transport.services.execution.transfer_execution_service._lock_task",
				return_value=self.task,
			)
		)
		self.enterContext(patch.object(service, "tasks_for_outgoing", return_value=[self.task]))
		self.receipts = self.enterContext(patch.object(service, "posted_receipts", return_value=[]))

	def receipt(self, qty=1, **values):
		return frappe._dict(
			name="IN-ROW",
			ste_detail="SED",
			against_stock_entry="OUT",
			item_code="Milk",
			qty=qty,
			uom="Carton",
			conversion_factor=10,
			transfer_qty=qty * 10,
			stock_uom="Pcs",
			s_warehouse="Transit",
			t_warehouse="Depot",
			batch_no="B1",
			serial_no="",
			serial_and_batch_bundle=None,
			receipt_name="IN",
			receipt_company="Company",
			receipt_user="receiver@example.test",
			receipt_time="2026-11-08 09:00:00",
			**values,
		)

	def document(self, rows):
		doc = SimpleNamespace(
			name="IN",
			outgoing_stock_entry="OUT",
			company="Company",
			add_to_transit=0,
			purpose="Material Transfer",
			items=rows,
		)
		doc.get = lambda key: getattr(doc, key, None)
		return doc

	def test_full_receipt_confirms_without_pod(self):
		self.receipts.return_value = [self.receipt(2)]
		state = service.receipt_summary(self.task)
		self.assertEqual(state["status"], "Completed")
		self.assertEqual(state["confirmation_method"], "Warehouse Receipt")
		self.assertEqual(state["receipt_stock_entry"], "IN")
		self.assertEqual(state["receipt_confirmed_by"], "receiver@example.test")

	def test_partial_receipts_accumulate_and_cancellation_reopens(self):
		self.receipts.return_value = [self.receipt()]
		self.assertEqual(service.receipt_summary(self.task)["status"], "Partially Received")
		self.receipts.return_value.append(self.receipt())
		self.assertEqual(service.receipt_summary(self.task)["status"], "Completed")
		self.receipts.return_value = []
		state = service.receipt_summary(self.task)
		self.assertEqual(state["status"], "Ready")
		self.assertIsNone(state["receipt_stock_entry"])

	def test_wrong_destination_company_and_unrelated_cargo_do_not_complete(self):
		for field, value in (
			("t_warehouse", "Other Depot"),
			("receipt_company", "Other Company"),
			("ste_detail", "UNRELATED"),
		):
			row = self.receipt(2)
			row[field] = value
			self.receipts.return_value = [row]
			self.assertEqual(service.receipt_summary(self.task)["status"], "Ready")

	def test_customer_and_return_pickup_require_their_original_proof(self):
		self.task.task_type = "Customer Delivery"
		self.assertIsNone(service.receipt_summary(self.task)["confirmation_method"])
		self.task.task_type = "Transfer Receipt"
		self.demand.source_document_type = "Return Request"
		self.assertIsNone(service.receipt_summary(self.task)["confirmation_method"])

	def test_later_pod_cannot_receive_the_same_partial_or_full_cargo(self):
		for qty in (1, 2):
			self.receipts.return_value = [self.receipt(qty)]
			with self.assertRaisesRegex(ValueError, "already has a warehouse receipt"):
				service.assert_no_manual_receipt(self.task)

	def test_valid_receipt_preserves_indirect_material_request_without_double_count(self):
		row = self.receipt()
		row.material_request, row.material_request_item = "MR", "MRI"
		service.validate_warehouse_receipt(self.document([row]))
		self.assertIsNone(row.material_request)
		self.assertEqual((row.ste_detail, row.against_stock_entry), ("SED", "OUT"))

	def test_wrong_depot_uom_batch_and_original_row_are_rejected(self):
		for field, value in (
			("t_warehouse", "Wrong"),
			("uom", "Pcs"),
			("batch_no", "B2"),
			("ste_detail", "Wrong"),
			("s_warehouse", "Wrong"),
		):
			row = self.receipt()
			row[field] = value
			with self.subTest(field=field), self.assertRaises(ValueError):
				service.validate_warehouse_receipt(self.document([row]))

	def test_duplicate_rows_and_previous_receipts_cannot_exceed_original_batch(self):
		with self.assertRaises(ValueError):
			service.validate_warehouse_receipt(self.document([self.receipt(1.5), self.receipt(1)]))
		self.receipts.return_value = [self.receipt(1)]
		with self.assertRaises(ValueError):
			service.validate_warehouse_receipt(self.document([self.receipt(1.5)]))

	def test_receipt_cannot_bypass_a_submitted_pod(self):
		self.framework.db.exists.return_value = True
		with self.assertRaisesRegex(ValueError, "already has submitted POD"):
			service.validate_warehouse_receipt(self.document([self.receipt()]))

	def test_older_task_resolves_exact_demand_row_through_load_item(self):
		self.task.items[0].transport_demand_item = None
		self.framework.db.get_value.return_value = "DI"
		service.validate_warehouse_receipt(self.document([self.receipt()]))
		self.framework.db.get_value.assert_called_with("Transport Load Item", "LI", "transport_demand_item")

	def test_serial_subsets_cannot_be_reused_or_changed(self):
		self.source.serial_no = "S1\nS2"
		row = self.receipt(0.1)
		row.serial_no = "S1"
		service.validate_warehouse_receipt(self.document([row]))
		row.serial_no = "S3"
		with self.assertRaises(ValueError):
			service.validate_warehouse_receipt(self.document([row]))
		row.serial_no = "S1"
		self.receipts.return_value = [row]
		with self.assertRaises(ValueError):
			service.validate_warehouse_receipt(self.document([row]))

	def test_draft_and_pod_linked_stock_do_not_trigger_manual_sync(self):
		for values in ({"docstatus": 0}, {"docstatus": 1, "vuna_transport_proof": "POD"}):
			doc = self.document([])
			for field, value in values.items():
				setattr(doc, field, value)
			with patch("vuna_transport.services.execution.trip_task_service.sync_task_execution") as sync:
				service.sync_warehouse_receipt(doc)
				sync.assert_not_called()

	def test_cancellation_resynchronizes_trip_and_clears_receipt_audit(self):
		doc = self.document([])
		doc.docstatus = 2
		with patch("vuna_transport.services.execution.trip_task_service.sync_task_execution") as sync:
			service.sync_warehouse_receipt(doc)
			sync.assert_called_once_with(self.task)
		self.assertIsNone(self.framework.db.set_value.call_args.args[2]["receipt_stock_entry"])

	def test_quantities_must_match_every_item_not_only_grand_total(self):
		self.assertEqual(service.receipt_progress({"A": 10, "B": 10}, {"A": 20}, 6), "Partially Received")
