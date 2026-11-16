"""Diversion execution preserves history and delegates only the approved receipt."""

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from vuna_transport.services.execution import shipment_diversion_execution as service
from vuna_transport.tests.test_shipment_diversion_documents import Document


class TestDiversionExecution(TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(service, "frappe"))
		self.framework.flags = frappe._dict()
		self.framework.db.exists.return_value = False
		self.framework.db.get_value.return_value = None
		self.framework.throw.side_effect = lambda message, *a, **k: self.fail_rule(message)
		self.enterContext(patch.object(service, "_", side_effect=lambda message: message))
		self.enterContext(patch.object(service, "require_document_permission"))
		self.item = Document(
			name="SDI",
			item_code="Milk",
			qty=2,
			uom="Carton",
			stock_uom="Pcs",
			stock_qty=48,
			conversion_factor=24,
		)
		self.doc = Document(
			doctype="Shipment Diversion",
			name="SD",
			return_request="RR",
			docstatus=1,
			company="Company",
			delivery_trip="TRIP",
			vehicle="Vehicle",
			driver="Driver",
			vehicle_warehouse="Vehicle WH",
			destination_type="Customer",
			customer="B",
			delivery_address="Address B",
			destination_warehouse="Depot WH",
			items=[self.item],
			db_set=MagicMock(),
		)
		self.trip = Document(
			name="TRIP",
			docstatus=1,
			company="Company",
			status="In Transit",
			vehicle="Vehicle",
			driver="Driver",
			vuna_actual_start="START",
			delivery_stops=[Document(name="OLD", idx=1, visited=1)],
			add_comment=MagicMock(),
		)
		self.target = Document(name="ONWARD", docstatus=1)
		self.context = self.enterContext(patch.object(service, "document_context", return_value=self.doc))
		self.trip_reader = self.enterContext(patch.object(service, "_trip", return_value=self.trip))
		self.existing = self.enterContext(
			patch.object(service, "existing_document", return_value=self.target)
		)
		self.enterContext(patch.object(service, "posted_return", return_value=Document(name="SR")))
		self.quality = self.enterContext(patch.object(service, "check_customer_quality"))
		self.stock = self.enterContext(patch.object(service, "check_vehicle_stock"))
		self.created = []

		def create(data, *args, **kwargs):
			if not isinstance(data, dict):
				return self.doc
			values = dict(data)
			values.update(name="NEW-" + data["doctype"], flags=frappe._dict(), insert=MagicMock())
			result = Document(**values)
			self.created.append(result)
			return result

		self.framework.get_doc.side_effect = create

	@staticmethod
	def fail_rule(message):
		raise ValueError(message)

	def test_customer_adds_one_stop_and_keeps_original_proof_history(self):
		name = service.add_diversion_to_trip("SD")
		stop, task = self.created
		self.assertEqual(name, task.name)
		self.assertEqual(
			(stop.idx, stop.customer, stop.address, stop.delivery_note), (2, "B", "Address B", "ONWARD")
		)
		self.assertEqual((task.task_type, task.shipment_diversion), ("Customer Delivery", "SD"))
		self.assertEqual(self.trip.delivery_stops[0].visited, 1)
		self.quality.assert_called_once()
		self.target.docstatus = 1
		self.framework.db.commit.assert_not_called()

	def test_assignment_retry_does_not_create_more_stops_or_tasks(self):
		self.framework.db.get_value.return_value = "Existing Task"
		self.assertEqual(service.add_diversion_to_trip("SD"), "Existing Task")
		self.assertFalse(self.created)

	def test_customer_requires_submitted_onward_note(self):
		for target in (None, Document(docstatus=0)):
			self.existing.return_value = target
			with self.assertRaisesRegex(ValueError, "Submit the onward"):
				service.add_diversion_to_trip("SD")
		self.assertFalse(self.created)

	def test_completed_trip_is_never_implicitly_reopened(self):
		self.trip.status = "Completed"
		with self.assertRaisesRegex(ValueError, "Resume.*explicitly"):
			service.add_diversion_to_trip("SD")
		self.assertFalse(self.created)

	def test_depot_adds_only_receiving_task_without_loading_stock_again(self):
		self.doc.destination_type, self.target.docstatus = "Depot", 0
		with patch(
			"vuna_transport.services.routing.location_service.get_warehouse_planning_data",
			return_value={"address": "Depot Address"},
		):
			service.add_diversion_to_trip("SD")
		stop, task = self.created
		self.assertEqual(task.task_type, "Transfer Receipt")
		self.assertEqual(task.dispatch_stock_entry, "ONWARD")
		self.assertEqual(stop.vuna_depot_names, "Depot WH")
		self.assertIsNone(stop.customer)
		self.assertFalse(any(row.get("task_type") == "Transfer Dispatch" for row in self.created))
		self.stock.assert_called_once_with(self.doc)

	def test_depot_requires_address_and_unposted_receipt(self):
		self.doc.destination_type = "Depot"
		with self.assertRaisesRegex(ValueError, "draft depot Stock Entry"):
			service.add_diversion_to_trip("SD")
		self.target.docstatus = 0
		with patch(
			"vuna_transport.services.routing.location_service.get_warehouse_planning_data", return_value={}
		):
			with self.assertRaisesRegex(ValueError, "warehouse address"):
				service.add_diversion_to_trip("SD")
		self.assertFalse(self.created)

	def proof_context(self):
		task = Document(name="TASK", delivery_stop="STOP", address="Depot Address")
		self.doc.execution_task = "TASK"
		self.item.batch_no, self.item.serial_no = None, ""
		entry = Document(
			name="SE", docstatus=0, flags=frappe._dict(), submit=MagicMock(), save=MagicMock(), items=[]
		)
		proof = Document(
			name="POD",
			docstatus=1,
			transport_trip_task="TASK",
			outcome="Completed",
			flags=frappe._dict(ignore_permissions=True),
			received_by="Receiver",
			remarks="",
			items=[
				Document(
					name="PODROW",
					source_row="SDI",
					delivered_qty=2,
					rejected_qty=0,
					serial_no="",
					rejected_serial_no="",
					depot_received_rejected_serial_no="",
					conversion_factor=24,
					uom="Carton",
					stock_uom="Pcs",
					missing_qty=0,
					batch_no=None,
				)
			],
		)
		self.enterContext(patch.object(service, "depot_context", return_value=(self.doc, self.trip, entry)))
		return task, entry, proof

	def test_mobile_posting_uses_exact_existing_entry_and_restores_delegation_flag(self):
		task, entry, proof = self.proof_context()
		with (
			patch(
				"vuna_transport.services.execution.transfer_execution_service._lock_task", return_value=task
			),
			patch(
				"vuna_transport.services.execution.driver_stock_policy.driver_end_transit_enabled",
				return_value=True,
			),
		):
			service.post_depot_proof(proof)
		entry.submit.assert_called_once_with()
		self.assertTrue(entry.flags.ignore_permissions)
		self.assertIsNone(self.framework.flags.vuna_diversion_receipt_proof)
		self.framework.new_doc.assert_not_called()

	def test_driver_posting_disabled_keeps_proof_but_does_not_post_stock(self):
		task, entry, proof = self.proof_context()
		with (
			patch(
				"vuna_transport.services.execution.transfer_execution_service._lock_task", return_value=task
			),
			patch(
				"vuna_transport.services.execution.driver_stock_policy.driver_end_transit_enabled",
				return_value=False,
			),
		):
			service.post_depot_proof(proof)
		entry.submit.assert_not_called()

	def test_failed_stock_submit_does_not_leave_privileged_posting_context(self):
		task, entry, proof = self.proof_context()
		entry.submit.side_effect = ValueError("Stock changed")
		with (
			patch(
				"vuna_transport.services.execution.transfer_execution_service._lock_task", return_value=task
			),
			patch(
				"vuna_transport.services.execution.driver_stock_policy.driver_end_transit_enabled",
				return_value=True,
			),
		):
			with self.assertRaises(ValueError):
				service.post_depot_proof(proof)
		self.assertIsNone(self.framework.flags.vuna_diversion_receipt_proof)

	def test_receipt_variance_can_be_recorded_without_posting_the_full_approved_stock(self):
		task, entry, proof = self.proof_context()
		with patch(
			"vuna_transport.services.execution.transfer_proof_service.validate_proof_items",
			return_value="Partially Completed",
		):
			service.validate_depot_proof(proof, task)
			self.assertEqual(proof.outcome, "Partially Completed")
		entry.submit.assert_not_called()
		proof.docstatus = 0
		with patch(
			"vuna_transport.services.execution.transfer_proof_service.validate_proof_items",
			return_value="Partially Completed",
		):
			service.validate_depot_proof(proof, task)

	def test_full_receipt_needs_started_trip_receiver_and_unique_proof(self):
		task, _entry, proof = self.proof_context()
		with patch(
			"vuna_transport.services.execution.transfer_proof_service.validate_proof_items",
			return_value="Completed",
		):
			service.validate_depot_proof(proof, task)
			self.assertEqual(proof.status, "Accepted")
			proof.received_by = ""
			with self.assertRaisesRegex(ValueError, "person confirming"):
				service.validate_depot_proof(proof, task)
			self.trip.vuna_actual_start = None
			with self.assertRaisesRegex(ValueError, "Start the trip"):
				service.validate_depot_proof(proof, task)

	def test_assignment_with_active_proof_cannot_be_removed(self):
		self.doc.execution_task = "TASK"
		task = Document(name="TASK", shipment_diversion="SD", delivery_stop="STOP")
		self.framework.get_doc.side_effect = [self.doc, task]
		self.framework.db.exists.return_value = True
		with self.assertRaisesRegex(ValueError, "proof before removing"):
			service.remove_diversion_assignment("SD")
		self.framework.db.delete.assert_not_called()

	def test_removing_assignment_archives_visit_before_removing_active_route_row(self):
		self.doc.execution_task = "TASK"
		events = []
		task = Document(
			name="TASK",
			shipment_diversion="SD",
			delivery_stop="STOP",
			sequence=2,
			db_set=MagicMock(),
			add_comment=MagicMock(side_effect=lambda *a: events.append("archive")),
		)
		stop = Document(name="STOP", parent="TRIP", parenttype="Delivery Trip", visited=1)
		self.framework.get_doc.side_effect = [self.doc, task, stop]
		self.framework.as_json.side_effect = frappe.as_json
		self.framework.db.delete.side_effect = lambda *a: events.append("remove")
		self.trip.reload = MagicMock()
		self.trip.db_set = MagicMock()
		with patch("vuna_transport.services.execution.trip_task_service.sync_task_execution"):
			self.assertEqual(service.remove_diversion_assignment("SD"), "TRIP")
		self.assertEqual(events, ["archive", "remove"])
		self.assertIn("&quot;visited&quot;: 1", task.add_comment.call_args.args[1])
		task.db_set.assert_called_once_with("status", "Cancelled", update_modified=True)
		self.doc.db_set.assert_called_once_with(
			{"execution_task": None, "execution_stop": None, "status": "Approved"}, update_modified=True
		)

	def test_resume_checks_custody_availability_and_keeps_original_stop_results(self):
		self.doc.custody_confirmed, self.doc.reason = 1, "Customer refused delivery"
		self.trip.status, self.trip.vuna_actual_completion = "Completed", "Previous completion"
		self.trip.db_set = MagicMock()
		self.framework.get_all.return_value = []
		with (
			patch("vuna_transport.services.execution.shipment_diversion_service._lock_diversion_source"),
			patch(
				"vuna_transport.services.execution.shipment_diversion_service._get_shipment_diversion_context",
				return_value={},
			),
			patch("vuna_transport.services.execution.shipment_diversion_service._diversion_context_checks"),
			patch(
				"vuna_transport.services.fleet.vehicle_availability_service.evaluate_vehicle_availability",
				return_value=frappe._dict(dispatch_eligible=True),
			),
			patch(
				"vuna_transport.services.fleet.driver_availability_service.evaluate_driver_availability",
				return_value=frappe._dict(dispatch_eligible=True),
			),
		):
			self.assertEqual(service.resume_original_trip("SD"), "TRIP")
		self.assertEqual(self.trip.delivery_stops[0].visited, 1)
		self.assertEqual(self.trip.db_set.call_args.args[0]["vuna_actual_completion"], None)
		self.doc.db_set.assert_called_once()

	def test_resume_does_not_steal_vehicle_or_driver_from_another_trip(self):
		self.doc.custody_confirmed, self.doc.reason = 1, "Refused"
		self.trip.status = "Completed"
		self.trip.db_set = MagicMock()
		self.framework.get_all.return_value = ["Another trip"]
		with (
			patch("vuna_transport.services.execution.shipment_diversion_service._lock_diversion_source"),
			patch(
				"vuna_transport.services.execution.shipment_diversion_service._get_shipment_diversion_context",
				return_value={},
			),
			patch("vuna_transport.services.execution.shipment_diversion_service._diversion_context_checks"),
		):
			with self.assertRaisesRegex(ValueError, "another active trip"):
				service.resume_original_trip("SD")
		self.trip.db_set.assert_not_called()

	def test_resume_requires_explicit_saved_custody_confirmation(self):
		self.doc.custody_confirmed, self.doc.reason = 0, "Refused"
		with patch("vuna_transport.services.execution.shipment_diversion_service._lock_diversion_source"):
			with self.assertRaisesRegex(ValueError, "confirm.*goods"):
				service.resume_original_trip("SD")
		self.doc.db_set.assert_not_called()

	def test_completed_diversion_cannot_resume_the_trip_again(self):
		self.doc.execution_task = "Existing Task"
		with patch("vuna_transport.services.execution.shipment_diversion_service._lock_diversion_source"):
			with self.assertRaisesRegex(ValueError, "execution history"):
				service.resume_original_trip("SD")
		self.doc.db_set.assert_not_called()

	def test_delegated_receipt_requires_submitted_proof_and_exact_approved_endpoints(self):
		task, entry, proof = self.proof_context()
		entry.company, entry.purpose = "Company", "Material Transfer"
		entry.docstatus = 1
		entry.add_to_transit, entry.outgoing_stock_entry = 0, None
		entry.vuna_shipment_diversion = "SD"
		entry.items = [
			Document(
				item_code="Milk",
				qty=2,
				transfer_qty=48,
				uom="Carton",
				stock_uom="Pcs",
				conversion_factor=24,
				vuna_diversion_item="SDI",
				serial_no="",
				batch_no=None,
				s_warehouse="Vehicle WH",
				t_warehouse="Depot WH",
			)
		]
		self.framework.get_doc.side_effect = (
			lambda doctype, name: proof if doctype == "Proof of Delivery" else task
		)
		self.framework.db.get_value.return_value = 48
		with patch("vuna_transport.services.execution.shipment_diversion_documents.validate_rows"):
			service.validate_delegated_receipt(entry, "POD")
			proof.docstatus = 0
			with self.assertRaisesRegex(ValueError, "exact depot receipt"):
				service.validate_delegated_receipt(entry, "POD")
			proof.docstatus = 1
			entry.items[0].t_warehouse = "Other"
			with self.assertRaises(frappe.ValidationError):
				service.validate_delegated_receipt(entry, "POD")
			entry.items[0].t_warehouse = "Depot WH"
			self.framework.db.get_value.return_value = 47
			with self.assertRaisesRegex(ValueError, "no longer available"):
				service.validate_delegated_receipt(entry, "POD")
