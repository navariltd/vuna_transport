"""Evidence quantities, identity preservation and stock-posting separation."""

import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.utils import flt

from vuna_transport.services.execution import transfer_proof_service as service


class TestTransferProof(unittest.TestCase):
	def setUp(self):
		self.enterContext(
			patch(
				"vuna_transport.services.execution.driver_stock_policy.driver_end_transit_enabled",
				return_value=True,
			)
		)
		self.enterContext(
			patch("vuna_transport.services.execution.warehouse_receipt_service.assert_no_manual_receipt")
		)

		def raise_validation(message):
			raise ValueError(message)

		self.framework = self.enterContext(patch.object(service, "frappe"))
		self.framework.throw.side_effect = raise_validation
		self.framework.get_precision.return_value = 6
		self.framework.get_cached_value.return_value = 0
		self.framework.as_json.side_effect = frappe.as_json
		self.enterContext(patch.object(service, "_", side_effect=lambda message: message))
		self.enterContext(
			patch.object(
				service,
				"flt",
				side_effect=lambda qty, precision=None: flt(
					qty, precision, rounding_method="Banker's Rounding"
				),
			)
		)
		self.planned = [
			dict(
				source_row="SED-1",
				batch_no="B-1",
				item_code="MILK",
				uom="Carton",
				stock_uom="Pcs",
				conversion_factor=10,
				ordered_qty=2,
				stock_qty=20,
				serial_no="",
				source_bundle="SBB-1",
			)
		]
		self.enterContext(
			patch.object(service, "planned_proof_items", side_effect=lambda stock: self.planned)
		)

	def row(self, qty=2, **values):
		return frappe._dict(source_row="SED-1", batch_no="B-1", delivered_qty=qty, serial_no="", **values)

	def test_full_receipt_preserves_cartons(self):
		row = self.row()
		self.assertEqual(service.validate_proof_items([row], None), "Completed")
		self.assertEqual((row.delivered_qty, row.uom, row.stock_qty), (2, "Carton", 20))

	def test_partial_receipt_does_not_discard_the_balance(self):
		row = self.row(0.5)
		self.assertEqual(service.validate_proof_items([row], None), "Partially Completed")
		self.assertEqual(row.stock_qty, 5)
		self.assertEqual(row.ordered_qty, 2)

	def test_failed_receipt_keeps_zero_quantity_row(self):
		self.assertEqual(service.validate_proof_items([self.row(0)], None), "Failed")

	def test_rejection_and_shortage_are_distinct(self):
		row = self.row(1)
		row.rejected_qty, row.rejection_reason_remarks = 1, "Damaged packaging"
		self.assertEqual(service.validate_proof_items([row], None), "Partially Completed")
		self.assertEqual((row.stock_qty, row.rejected_stock_qty, row.missing_qty), (10, 10, 0))
		row.rejected_qty = 0
		service.validate_proof_items([row], None)
		self.assertEqual((row.rejected_stock_qty, row.missing_qty), (0, 1))

	def test_accepted_plus_rejected_cannot_exceed_dispatch(self):
		row = self.row(2)
		row.rejected_qty, row.rejection_reason_remarks = 1, "Damaged"
		with self.assertRaises(ValueError):
			service.validate_proof_items([row], None)

	def test_explicit_rejection_requires_reason(self):
		row = self.row(1)
		row.rejected_qty = 1
		with self.assertRaises(ValueError):
			service.validate_proof_items([row], None)

	def submit_proof(self, delivered=1, rejected=1, reason="Damaged", remarks=""):
		doc = SimpleNamespace(
			get_doc_before_save=lambda: None,
			flags=frappe._dict(ignore_permissions=True),
			transport_trip_task="TASK-1",
			name="POD-1",
			items=[self.row(delivered, rejected_qty=rejected, rejection_reason=reason)],
			remarks=remarks,
			delivery_datetime=None,
			docstatus=1,
			received_by="Receiver",
		)
		task = frappe._dict(name="TASK-1", task_type="Transfer Receipt")
		trip = frappe._dict(name="TRIP-1", vuna_actual_start="2026-11-04")
		self.framework.db.get_value.return_value = None
		with (
			patch.object(service, "_lock_task", return_value=task),
			patch.object(service, "validate_depot_custody"),
			patch.object(service, "proof_context", return_value=(trip, frappe._dict(name="SE-1"))),
			patch.object(service, "now_datetime", return_value="2026-11-04 12:00:00"),
		):
			service.validate_proof(doc)
		return doc

	def test_partial_rejection_with_reason_needs_no_duplicate_remarks(self):
		self.assertEqual(self.submit_proof().status, "Partially Delivered")

	def test_full_rejection_with_reason_needs_no_duplicate_remarks(self):
		self.assertEqual(self.submit_proof(delivered=0, rejected=2).status, "Rejected")

	def test_unaccounted_shortage_still_requires_remarks(self):
		with self.assertRaisesRegex(ValueError, "missing or unaccounted"):
			self.submit_proof(delivered=1, rejected=0)

	def test_rejection_reason_does_not_explain_separate_missing_goods(self):
		with self.assertRaisesRegex(ValueError, "missing or unaccounted"):
			self.submit_proof(delivered=0.5, rejected=1)

	def test_explained_shortage_can_be_submitted(self):
		self.assertEqual(
			self.submit_proof(delivered=1, rejected=0, remarks="One carton missing").status,
			"Partially Delivered",
		)

	def test_unexplained_full_failure_still_requires_remarks(self):
		with self.assertRaisesRegex(ValueError, "missing or unaccounted"):
			self.submit_proof(delivered=0, rejected=0, remarks="  ")

	def test_missing_goods_do_not_automatically_create_return_request(self):
		from vuna_transport.services.execution import depot_return_service

		pod = SimpleNamespace(
			docstatus=1, proof_type="Transfer Receipt", items=[frappe._dict(rejected_qty=0)]
		)
		with patch.object(depot_return_service, "frappe") as framework:
			self.assertIsNone(depot_return_service.create_depot_return_from_pod(pod))
			framework.new_doc.assert_not_called()

	def test_missing_duplicate_wrong_batch_and_over_receipt_rejected(self):
		for rows in (
			[],
			[self.row(), self.row()],
			[frappe._dict(source_row="SED-1", batch_no="OTHER", delivered_qty=2)],
			[self.row(3)],
		):
			with self.assertRaises(ValueError):
				service.validate_proof_items(rows, None)

	def test_negative_and_nonfinite_receipts_rejected(self):
		for qty in (-1, float("nan"), float("inf")):
			with self.assertRaises(ValueError):
				service.validate_proof_items([self.row(qty)], None)

	def test_dispatch_proof_must_match_posted_stock(self):
		with self.assertRaises(ValueError):
			service.validate_proof_items([self.row(1)], None, dispatch=True)

	def test_multiple_batches_have_independent_limits(self):
		self.planned.append({**self.planned[0], "batch_no": "B-2", "stock_qty": 10, "ordered_qty": 1})
		second = frappe._dict(source_row="SED-1", batch_no="B-2", delivered_qty=1, serial_no="")
		self.assertEqual(service.validate_proof_items([self.row(1), second], None), "Partially Completed")

	def test_serial_subset_must_match_actual_stock_qty(self):
		self.planned[0]["serial_no"] = "S1\nS2"
		self.planned[0]["stock_qty"] = 2
		self.planned[0]["ordered_qty"] = 0.2
		row = self.row(0.1)
		row.serial_no = "S1"
		self.assertEqual(service.validate_proof_items([row], None), "Partially Completed")
		for serials in ("", "S3", "S1\nS1", "S1\nS2"):
			row.serial_no = serials
			with self.assertRaises(ValueError):
				service.validate_proof_items([row], None)

	def test_accepted_and_rejected_serials_cannot_overlap(self):
		self.planned[0].update(serial_no="S1\nS2", stock_qty=2, ordered_qty=0.2)
		row = self.row(0.1)
		row.serial_no, row.rejected_serial_no = "S1", "S2"
		row.rejected_qty, row.rejection_reason_remarks = 0.1, "Damaged"
		service.validate_proof_items([row], None)
		row.rejected_serial_no = "S1"
		with self.assertRaises(ValueError):
			service.validate_proof_items([row], None)

	def test_bundle_identity_precedes_legacy_fields(self):
		self.framework.get_doc.return_value = SimpleNamespace(
			entries=[
				frappe._dict(batch_no="B-1", qty=-7, serial_no=None),
				frappe._dict(batch_no="B-2", qty=-3, serial_no=None),
			]
		)
		row = frappe._dict(serial_and_batch_bundle="SBB-1", batch_no="WRONG", serial_no="", transfer_qty=10)
		self.assertEqual(
			service.stock_identities(row),
			{"B-1": {"stock_qty": 7, "serials": []}, "B-2": {"stock_qty": 3, "serials": []}},
		)

	def test_legacy_identity_is_retained_without_bundle(self):
		row = frappe._dict(
			serial_and_batch_bundle=None, batch_no="B-1", serial_no="S1\nS2", transfer_delivered_qty=2
		)
		self.assertEqual(service.stock_identities(row)["B-1"]["serials"], ["S1", "S2"])

	def test_receipt_proof_waits_for_stock_posting(self):
		self.framework.db.get_value.return_value = frappe._dict(name="PROOF-1", outcome="Completed")
		self.framework.db.exists.return_value = False
		self.assertEqual(
			service.derive_transfer_status(frappe._dict(name="TASK", task_type="Transfer Receipt")),
			"Awaiting Stock Posting",
		)
		self.framework.db.exists.return_value = True
		self.assertEqual(
			service.derive_transfer_status(frappe._dict(name="TASK", task_type="Transfer Receipt")),
			"Completed",
		)

	def test_historical_loading_is_satisfied_by_stock_not_a_loading_pod(self):
		self.framework.db.get_value.return_value = 1
		with patch.object(service, "get_dispatch_entry", return_value="SE-1"):
			self.assertEqual(
				service.derive_transfer_status(frappe._dict(name="OLD", task_type="Transfer Dispatch")),
				"Completed",
			)
		self.framework.db.get_value.assert_called_once_with("Stock Entry", "SE-1", "docstatus")

	def test_failed_attempt_does_not_require_a_stock_receipt(self):
		self.framework.db.get_value.return_value = frappe._dict(name="PROOF-1", outcome="Failed")
		self.framework.db.exists.return_value = False
		self.assertEqual(
			service.derive_transfer_status(frappe._dict(name="TASK", task_type="Transfer Receipt")), "Failed"
		)

	def test_offline_retry_returns_same_submitted_proof(self):
		payload = {
			"request_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
			"confirmed_by": "Receiver",
			"items": [],
		}
		proof = frappe._dict(
			name="PROOF-1",
			transport_trip_task="TASK-1",
			docstatus=1,
			outcome="Completed",
			payload_hash=hashlib.sha256(frappe.as_json(payload).encode()).hexdigest(),
		)
		self.framework.db.get_value.return_value = proof.name
		self.framework.get_doc.return_value = proof
		with (
			patch(
				"vuna_transport.services.mobile.mobile_device_service._require_mobile_driver",
				return_value=frappe._dict(name="DR-1"),
			),
			patch("vuna_transport.services.mobile.mobile_device_service._get_assigned_trip"),
			patch.object(
				service, "_lock_task", return_value=frappe._dict(name="TASK-1", delivery_trip="TRIP-1")
			),
		):
			self.assertEqual(
				service.submit_mobile_transfer_proof("DEVICE", "TASK-1", payload),
				{"name": "PROOF-1", "outcome": "Completed"},
			)
			proof.payload_hash = "changed"
			with self.assertRaises(ValueError):
				service.submit_mobile_transfer_proof("DEVICE", "TASK-1", payload)

	def test_unassigned_driver_cannot_submit_proof(self):
		with (
			patch(
				"vuna_transport.services.mobile.mobile_device_service._require_mobile_driver",
				return_value=frappe._dict(name="DR-1"),
			),
			patch(
				"vuna_transport.services.mobile.mobile_device_service._get_assigned_trip",
				side_effect=PermissionError,
			),
			patch.object(
				service, "_lock_task", return_value=frappe._dict(name="TASK-1", delivery_trip="TRIP-1")
			),
		):
			with self.assertRaises(PermissionError):
				service.submit_mobile_transfer_proof("DEVICE", "TASK-1", {})

	def test_pod_automatically_inserts_and_submits_derived_receipt(self):
		proof = SimpleNamespace(
			docstatus=1, proof_type="Transfer Receipt", transport_trip_task="TASK-1", items=[self.row(1)]
		)
		entry = MagicMock(docstatus=0)
		entry.name = "SE-RECEIPT"
		entry.is_new.return_value = True
		with patch.object(service, "_make_receipt_stock_entry", return_value=entry) as make:
			self.assertEqual(service.submit_receipt_stock_from_pod(proof), "SE-RECEIPT")
		make.assert_called_once_with(proof, check_permissions=False)
		entry.insert.assert_called_once_with(ignore_permissions=True)
		entry.submit.assert_called_once_with()
		self.assertTrue(entry.flags.ignore_permissions)

	def test_automatic_receipt_reuses_submitted_entry(self):
		proof = SimpleNamespace(
			docstatus=1, proof_type="Transfer Receipt", transport_trip_task="TASK-1", items=[self.row(1)]
		)
		entry = MagicMock(docstatus=1)
		with patch.object(service, "_make_receipt_stock_entry", return_value=entry):
			service.submit_receipt_stock_from_pod(proof)
		entry.insert.assert_not_called()
		entry.submit.assert_not_called()

	def test_full_rejection_does_not_create_zero_quantity_stock_entry(self):
		proof = SimpleNamespace(
			docstatus=1,
			proof_type="Transfer Receipt",
			transport_trip_task="TASK-1",
			items=[self.row(0, rejected_qty=2)],
		)
		with patch.object(service, "_make_receipt_stock_entry") as make:
			self.assertIsNone(service.submit_receipt_stock_from_pod(proof))
		make.assert_not_called()

	def test_stock_submission_failure_propagates_to_pod_transaction(self):
		proof = SimpleNamespace(
			docstatus=1, proof_type="Transfer Receipt", transport_trip_task="TASK-1", items=[self.row(1)]
		)
		entry = MagicMock(docstatus=0)
		entry.submit.side_effect = ValueError("Quality Inspection required")
		with (
			patch.object(service, "_make_receipt_stock_entry", return_value=entry),
			self.assertRaisesRegex(ValueError, "Quality Inspection required"),
		):
			service.submit_receipt_stock_from_pod(proof)
