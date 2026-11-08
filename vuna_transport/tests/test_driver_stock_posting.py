"""Driver proofs grant only configured, task-scoped stock posting."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from vuna_transport.services.execution import driver_stock_policy as policy
from vuna_transport.services.execution import supplier_collection_proof_service as service
from vuna_transport.services.execution import transfer_proof_service as transfers


def raise_validation(message):
	raise ValueError(message)


class TestDriverStockPolicy(TestCase):
	def test_independent_defaults_and_explicit_disable(self):
		for values, expected in (
			({}, (False, True)),
			({"allow_driver_purchase_receipt": 1, "allow_driver_end_transit": 0}, (True, False)),
			({"allow_driver_purchase_receipt": 0, "allow_driver_end_transit": 1}, (False, True)),
		):
			with patch.object(policy.frappe, "get_cached_doc", return_value=frappe._dict(values)):
				self.assertEqual(
					(policy.driver_purchase_receipts_enabled(), policy.driver_end_transit_enabled()), expected
				)

	def test_settings_metadata_defaults_preserve_depot_posting(self):
		path = (
			Path(__file__).resolve().parents[1]
			/ "vuna_transport/doctype/transport_settings/transport_settings.json"
		)
		doc = json.loads(path.read_text())
		fields = {row["fieldname"]: row for row in doc["fields"]}
		self.assertEqual(fields["allow_driver_purchase_receipt"]["default"], "0")
		self.assertEqual(fields["allow_driver_end_transit"]["default"], "1")
		for name in ("allow_driver_purchase_receipt", "allow_driver_end_transit"):
			self.assertIn(name, doc["field_order"])

	def test_disabled_end_transit_does_not_map_or_post_stock(self):
		with (
			patch.object(policy, "driver_end_transit_enabled", return_value=False),
			patch.object(transfers, "_make_receipt_stock_entry") as mapper,
		):
			self.assertIsNone(
				transfers.submit_receipt_stock_from_pod(
					SimpleNamespace(docstatus=1, proof_type="Transfer Receipt")
				)
			)
			mapper.assert_not_called()

	def test_initialization_preserves_explicit_disabled_setting(self):
		with patch.object(policy, "frappe") as framework:
			framework.db.get_singles_dict.return_value = {"allow_driver_end_transit": "0"}
			policy.initialize_driver_stock_policy()
			framework.db.set_single_value.assert_called_once_with(
				"Transport Settings", "allow_driver_purchase_receipt", 0
			)


class TestSupplierCollection(TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(service, "frappe"))
		self.framework.throw.side_effect = raise_validation
		self.framework.db.exists.return_value = False
		self.framework.db.get_value.return_value = None
		self.framework.get_precision.return_value = 6
		self.framework.get_cached_value.return_value = (0, 0)
		self.framework.session.user = "driver@example.test"
		self.enterContext(patch.object(service, "_", side_effect=lambda value: value))
		self.task = frappe._dict(
			name="TASK",
			delivery_trip="TRIP",
			delivery_stop="STOP",
			task_type="Supplier Pickup",
			company="Company",
			transport_load="LOAD",
			address="Supplier address",
		)
		self.trip = frappe._dict(name="TRIP", vuna_actual_start="2026-11-08 10:00:00")
		self.demand = frappe._dict(name="TD")
		self.order = frappe._dict(name="PO")
		self.allocation = frappe._dict(name="VA")
		self.context = self.enterContext(
			patch.object(
				service,
				"collection_context",
				return_value=(self.trip, self.demand, self.order, self.allocation, "VEHICLE", []),
			)
		)
		self.enterContext(
			patch(
				"vuna_transport.services.execution.transfer_execution_service._lock_task",
				return_value=self.task,
			)
		)
		self.enterContext(patch.object(service, "assert_no_receipt"))
		self.plan = {
			"source_row": "POI",
			"item_code": "MILK",
			"item_name": "Milk",
			"ordered_qty": 2,
			"delivered_qty": 2,
			"uom": "Carton",
			"stock_uom": "Pcs",
			"conversion_factor": 10,
		}
		self.planned = self.enterContext(
			patch.object(service, "planned_collection_items", return_value=[self.plan])
		)
		self.enterContext(patch.object(service, "now_datetime", return_value="2026-11-08 10:00:00"))
		row = frappe._dict(source_row="POI", delivered_qty=2, rejected_qty=0, serial_no="", batch_no=None)
		row.set = lambda field, value: row.__setitem__(field, value)
		self.proof = SimpleNamespace(
			name="POD",
			transport_trip_task="TASK",
			flags=frappe._dict(ignore_permissions=True),
			get_doc_before_save=lambda: None,
			items=[row],
			docstatus=1,
			received_by="Supplier clerk",
			remarks="",
			delivery_datetime=None,
			proof_type="Supplier Pickup",
		)

	def test_collection_preserves_transaction_uom_and_canonical_stock_qty(self):
		service.validate_collection(self.proof)
		row = self.proof.items[0]
		self.assertEqual((row.delivered_qty, row.uom, row.stock_qty), (2, "Carton", 20))
		self.assertEqual((self.proof.direction, self.proof.outcome), ("Pickup", "Completed"))

	def test_partial_collection_needs_remarks_and_keeps_missing_balance(self):
		self.proof.items[0].delivered_qty = 1
		with self.assertRaisesRegex(ValueError, "Explain"):
			service.validate_collection(self.proof)
		self.proof.remarks = "Supplier only released one carton"
		service.validate_collection(self.proof)
		self.assertEqual(self.proof.items[0].missing_qty, 1)
		self.assertEqual(self.proof.outcome, "Partially Completed")

	def test_invalid_quantities_and_rejected_quantities_are_not_received(self):
		for quantity in (-1, 3, float("nan"), float("inf")):
			self.proof.items[0].delivered_qty = quantity
			with self.assertRaises(ValueError):
				service.validate_collection(self.proof)
		self.proof.items[0].delivered_qty = 2
		self.proof.items[0].rejected_qty = 1
		with self.assertRaises(ValueError):
			service.validate_collection(self.proof)

	def test_unknown_missing_and_duplicate_source_rows_are_rejected(self):
		self.proof.items[0].source_row = "OTHER"
		with self.assertRaises(ValueError):
			service.validate_collection(self.proof)
		self.proof.items[0].source_row = "POI"
		self.proof.items.append(self.proof.items[0])
		with self.assertRaises(ValueError):
			service.validate_collection(self.proof)
		self.proof.items = []
		with self.assertRaises(ValueError):
			service.validate_collection(self.proof)

	def test_trip_must_be_started_and_release_person_required(self):
		self.trip.vuna_actual_start = None
		with self.assertRaisesRegex(ValueError, "Start"):
			service.validate_collection(self.proof)
		self.trip.vuna_actual_start = "2026-11-08"
		self.proof.received_by = ""
		with self.assertRaisesRegex(ValueError, "person"):
			service.validate_collection(self.proof)

	def test_disabled_supplier_receiving_still_allows_proof_but_posts_nothing(self):
		service.validate_collection(self.proof)
		self.context.reset_mock()
		with patch.object(policy, "driver_purchase_receipts_enabled", return_value=False):
			self.assertIsNone(service.post_collection_receipt(self.proof))
		self.context.assert_not_called()

	def test_existing_receipt_prevents_duplicate_posting(self):
		self.framework.db.exists.return_value = True
		with patch.object(policy, "driver_purchase_receipts_enabled", return_value=True):
			with self.assertRaisesRegex(ValueError, "existing"):
				service.post_collection_receipt(self.proof)

	def test_receipt_posts_only_collected_quantities_into_vehicle(self):
		service.validate_collection(self.proof)
		receipt = MagicMock()
		receipt.name = "PR"
		receipt.items = [frappe._dict(purchase_order_item="POI", warehouse="Destination")]
		with (
			patch.object(policy, "driver_purchase_receipts_enabled", return_value=True),
			patch("frappe.model.mapper.get_mapped_doc", return_value=receipt) as mapper,
			patch("erpnext.buying.doctype.purchase_order.purchase_order.set_missing_values"),
			patch(
				"vuna_transport.services.execution.allocation_transfer_service.set_allocation_stock_defaults"
			),
		):
			self.assertEqual(service.post_collection_receipt(self.proof), "PR")
		self.assertTrue(mapper.call_args.kwargs["ignore_permissions"])
		self.assertEqual(
			(receipt.vuna_pickup_task, receipt.vuna_pickup_transit_warehouse), ("TASK", "VEHICLE")
		)
		self.assertEqual(
			(receipt.items[0].qty, receipt.items[0].warehouse, receipt.items[0].stock_qty), (2, "VEHICLE", 20)
		)
		receipt.insert.assert_called_once_with(ignore_permissions=True)
		receipt.submit.assert_called_once()

	def test_receipt_validation_failure_propagates_without_committing_proof(self):
		service.validate_collection(self.proof)
		receipt = MagicMock()
		receipt.items = [frappe._dict(purchase_order_item="POI", warehouse="Destination")]
		receipt.submit.side_effect = ValueError("QI required")
		with (
			patch.object(policy, "driver_purchase_receipts_enabled", return_value=True),
			patch("frappe.model.mapper.get_mapped_doc", return_value=receipt),
			patch("erpnext.buying.doctype.purchase_order.purchase_order.set_missing_values"),
			patch(
				"vuna_transport.services.execution.allocation_transfer_service.set_allocation_stock_defaults"
			),
		):
			with self.assertRaisesRegex(ValueError, "QI required"):
				service.post_collection_receipt(self.proof)
		self.framework.db.commit.assert_not_called()

	def test_cancelling_collection_requires_receipt_reversal_first(self):
		self.framework.db.exists.return_value = True
		with self.assertRaisesRegex(ValueError, "Purchase Receipt"):
			service.guard_collection_cancellation(self.proof)

	def test_proof_waits_for_posting_then_completes_with_correct_variance(self):
		service.validate_collection(self.proof)
		self.framework.db.get_value.return_value = "POD"
		self.framework.get_doc.return_value = self.proof
		self.assertEqual(service.collection_status(self.task, {"received": {}}), "Awaiting Stock Posting")
		self.assertEqual(service.collection_status(self.task, {"received": {"POI": 20}}), "Completed")
		self.proof.outcome, self.proof.items[0].stock_qty = "Partially Completed", 10
		self.assertEqual(
			service.collection_status(self.task, {"received": {"POI": 10}}), "Completed with Variance"
		)

	def test_failed_collection_does_not_require_stock_receipt(self):
		self.framework.db.get_value.return_value = "POD"
		self.framework.get_doc.return_value = self.proof
		self.proof.outcome = "Failed"
		self.assertEqual(service.collection_status(self.task, {"received": {}}), "Failed")
