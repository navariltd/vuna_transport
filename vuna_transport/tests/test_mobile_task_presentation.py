"""Driver-facing task context is derived without creating stops or transactions."""

from unittest import TestCase
from unittest.mock import patch

import frappe

from vuna_transport.services.mobile import mobile_device_service as service


class TestMobileTaskPresentation(TestCase):
	def setUp(self):
		self.rows = {}
		self.enterContext(
			patch.object(
				service.frappe, "get_all", side_effect=lambda doctype, **kw: self.rows.get(doctype, [])
			)
		)
		self.enterContext(patch.object(service.frappe, "get_cached_value", return_value="Milk"))

	def task(self, **values):
		return frappe._dict(name="TASK", task_type="Transfer Receipt", transport_demand="TD", **values)

	def demand(self, **values):
		self.rows["Transport Demand"] = [
			frappe._dict(name="TD", source_warehouse="Main", destination_warehouse="Depot", **values)
		]

	def test_depot_delivery_exposes_stock_document_destination_and_requested_uom(self):
		self.demand(source_document_type="Material Request", source_document="MR")
		self.rows["Transport Trip Task Item"] = [
			frappe._dict(parent="TASK", item_code="MILK", qty=2, uom="Carton")
		]
		task = self.task(dispatch_stock_entry="STE")
		service._attach_task_presentation([task])
		self.assertEqual(
			(task.reference_doctype, task.reference_name, task.party_name), ("Stock Entry", "STE", "Depot")
		)
		self.assertEqual(
			task["items"][0], {"item_code": "MILK", "item_name": "Milk", "qty": 2, "uom": "Carton"}
		)

	def test_diversion_delivery_uses_existing_depot_card_without_a_transport_demand(self):
		task = frappe._dict(
			name="TASK", task_type="Transfer Receipt", shipment_diversion="SD", dispatch_stock_entry="SE"
		)
		with patch.object(service.frappe.db, "get_value", return_value="Receiving Depot"):
			service._attach_task_presentation([task])
		self.assertEqual(
			(task.reference_doctype, task.reference_name, task.party_name),
			("Stock Entry", "SE", "Receiving Depot"),
		)

	def test_direct_customer_pickup_exposes_return_request(self):
		self.rows["Return Request"] = [
			frappe._dict(name="RR", return_context="Customer", customer="Customer")
		]
		task = frappe._dict(name="TASK", task_type="Return Pickup", return_request="RR")
		service._attach_task_presentation([task])
		self.assertEqual(
			(task.reference_doctype, task.reference_name, task.party_name),
			("Return Request", "RR", "Customer"),
		)

	def test_supplier_pickup_exposes_po_without_blocking_collection_evidence(self):
		self.demand(source_document_type="Purchase Order", source_document="PO")
		task = frappe._dict(name="TASK", task_type="Supplier Pickup", transport_demand="TD", status="Ready")
		with patch.object(service.frappe.db, "get_value", return_value="Supplier"):
			service._attach_task_presentation([task])
		self.assertEqual(
			(task.reference_doctype, task.reference_name, task.party_name),
			("Purchase Order", "PO", "Supplier"),
		)
		self.assertFalse(task.blocked_reason)
		task.status = "Completed"
		with patch.object(service.frappe.db, "get_value", return_value="Supplier"):
			service._attach_task_presentation([task])
		self.assertFalse(task.blocked_reason)

	def test_return_receiving_is_blocked_until_pickup_posts_dispatch_stock(self):
		self.demand(source_document_type="Return Request", source_document="RR")
		task = self.task()
		service._attach_task_presentation([task])
		self.assertIn("pickup", task.blocked_reason)
		posted = self.task(dispatch_stock_entry="STE")
		service._attach_task_presentation([posted])
		self.assertFalse(posted.blocked_reason)

	def test_pickup_only_task_does_not_hide_customer_delivery_action(self):
		task = frappe._dict(
			name="TASK", delivery_stop="STOP", task_type="Return Pickup", status="Ready", return_request="RR"
		)
		stop = frappe._dict(name="STOP", customer="Customer", delivery_note="DN", can_complete_delivery=True)
		trip = frappe._dict(name="TRIP", vuna_task_based=0, vuna_actual_start="2026-11-05")
		with (
			patch.object(service.frappe.db, "exists", return_value=True),
			patch.object(service.frappe, "get_all", side_effect=[[task], []]),
			patch.object(service, "_attach_task_presentation"),
		):
			service._attach_transport_tasks(trip, [stop])
		self.assertTrue(stop.can_complete_delivery)
		self.assertTrue(stop.tasks[0].can_record_proof)
