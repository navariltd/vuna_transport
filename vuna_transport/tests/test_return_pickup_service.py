import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.utils import flt

from vuna_transport.services.execution import return_pickup_service as service


class TestReturnPickupProof(unittest.TestCase):
	def test_pickup_stock_cancellation_preserves_erp_exclusions(self):
		entry = SimpleNamespace(
			name="SE-1", vuna_return_pickup_proof="POD-1", ignore_linked_doctypes=["GL Entry"]
		)
		entry.get = lambda key: getattr(entry, key, None)
		service.guard_pickup_stock_cancellation(entry)
		self.assertTrue(
			{"GL Entry", "Proof of Delivery", "Transport Trip Task"}.issubset(
				set(entry.ignore_linked_doctypes)
			)
		)

	def test_pickup_stock_cancellation_still_requires_end_transit_cancellation(self):
		entry = SimpleNamespace(name="SE-1", vuna_return_pickup_proof="POD-1")
		entry.get = lambda key: getattr(entry, key, None)
		self.framework.db.exists.return_value = True
		with self.assertRaisesRegex(ValueError, "End Transit"):
			service.guard_pickup_stock_cancellation(entry)

	def test_other_receiving_proofs_are_not_ignored_on_stock_cancellation(self):
		entry = SimpleNamespace(name="SE-1", vuna_return_pickup_proof="POD-1")
		entry.get = lambda key: getattr(entry, key, None)
		self.framework.db.exists.side_effect = lambda doctype, *args: doctype == "Proof of Delivery"
		with self.assertRaisesRegex(ValueError, "receiving POD"):
			service.guard_pickup_stock_cancellation(entry)

	def setUp(self):
		self.framework = self.enterContext(patch.object(service, "frappe"))
		self.framework.db.exists.return_value = False
		self.framework.get_precision.return_value = 6

		def fail(message):
			raise ValueError(message)

		self.framework.throw.side_effect = fail
		self.enterContext(patch.object(service, "_", side_effect=lambda message: message))
		self.enterContext(patch.object(service, "now_datetime", return_value="2026-11-04 12:00:00"))
		self.enterContext(
			patch.object(
				service,
				"flt",
				side_effect=lambda value, precision=None: flt(
					value, precision, rounding_method="Banker's Rounding"
				),
			)
		)
		self.task = frappe._dict(name="TASK-1", company="Company", transport_load="TL-1", address="Depot")
		self.enterContext(
			patch(
				"vuna_transport.services.execution.transfer_execution_service._lock_task",
				return_value=self.task,
			)
		)
		self.trip = frappe._dict(name="TRIP-1", vuna_actual_start="2026-11-04")
		request = SimpleNamespace(name="RR-1", items=[frappe._dict(name="RRI-1", requested_return_qty=4)])
		self.enterContext(
			patch.object(service, "pickup_context", return_value=(self.trip, None, request, None))
		)
		self.planned = {
			"source_row": "RRI-1",
			"item_code": "MILK",
			"item_name": "Milk",
			"ordered_qty": 4,
			"stock_qty": 40,
			"uom": "Carton",
			"stock_uom": "Pcs",
			"conversion_factor": 10,
			"batch_no": "BATCH-1",
			"serial_no": "",
			"source_bundle": None,
		}
		self.enterContext(
			patch.object(service, "planned_pickup_items", side_effect=lambda task: [self.planned])
		)
		self.posted = self.enterContext(
			patch(
				"vuna_transport.services.demand.depot_return_demand_service.posted_pickup_quantities",
				return_value={},
			)
		)
		self.row = SimpleNamespace(source_row="RRI-1", delivered_qty=2, rejected_qty=0, serial_no="")
		self.row.set = lambda key, value: setattr(self.row, key, value)
		self.proof = SimpleNamespace(
			name="POD-1",
			transport_trip_task="TASK-1",
			flags=frappe._dict(ignore_permissions=True),
			get_doc_before_save=lambda: None,
			items=[self.row],
			docstatus=1,
			delivery_datetime=None,
			received_by="Depot Clerk",
			remarks="Only half collected",
		)

	def test_partial_pickup_preserves_uom_and_uncollected_balance(self):
		service.validate_pickup_proof(self.proof)
		self.assertEqual((self.row.delivered_qty, self.row.stock_qty, self.row.missing_qty), (2, 20, 2))
		self.assertEqual((self.row.uom, self.row.batch_no), ("Carton", "BATCH-1"))
		self.assertEqual(self.proof.outcome, "Partially Completed")

	def test_pickup_cannot_exceed_plan(self):
		self.row.delivered_qty = 5
		with self.assertRaises(ValueError):
			service.validate_pickup_proof(self.proof)

	def test_other_pickups_reduce_outstanding_quantity(self):
		self.posted.return_value = {"RRI-1": 30}
		with self.assertRaises(ValueError):
			service.validate_pickup_proof(self.proof)

	def test_wrong_source_item_is_rejected(self):
		self.row.source_row = "OTHER"
		with self.assertRaises(ValueError):
			service.validate_pickup_proof(self.proof)

	def test_duplicate_source_item_is_rejected(self):
		self.proof.items.append(self.row)
		with self.assertRaises(ValueError):
			service.validate_pickup_proof(self.proof)

	def test_pickup_requires_a_started_trip(self):
		self.trip.vuna_actual_start = None
		with self.assertRaises(ValueError):
			service.validate_pickup_proof(self.proof)

	def test_partial_pickup_needs_an_explanation(self):
		self.proof.remarks = ""
		with self.assertRaisesRegex(ValueError, "uncollected"):
			service.validate_pickup_proof(self.proof)

	def test_failed_pickup_does_not_create_stock(self):
		self.row.delivered_qty = 0
		service.validate_pickup_proof(self.proof)
		self.assertEqual(self.proof.outcome, "Failed")
		with patch.object(service, "pickup_context") as context:
			service.post_pickup_stock(self.proof)
		context.assert_not_called()

	def test_pickup_stock_cannot_clear_its_source_link(self):
		entry = SimpleNamespace(
			get_doc_before_save=lambda: frappe._dict(vuna_return_pickup_proof="POD-1"), get=lambda key: None
		)
		with self.assertRaisesRegex(ValueError, "cannot be changed"):
			service.validate_pickup_stock(entry)

	def test_serial_pickup_requires_exact_collected_subset(self):
		self.planned.update(conversion_factor=1, stock_qty=4, serial_no="S1\nS2\nS3\nS4")
		self.row.serial_no = "S1\nS2"
		service.validate_pickup_proof(self.proof)
		self.assertEqual(self.row.stock_qty, 2)
		self.row.serial_no = "S1\nOTHER"
		with self.assertRaises(ValueError):
			service.validate_pickup_proof(self.proof)
