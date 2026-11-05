"""Task POD evidence shares customer POD authorization and private attachments."""

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from vuna_transport.api import prepare_mobile_transport_proof
from vuna_transport.services.mobile import mobile_device_service as mobile


class TestTransportEvidenceDraft(TestCase):
	def setUp(self):
		self.proof = MagicMock()
		self.proof.name, self.proof.docstatus = "POD", 0
		self.proof.items, self.proof.return_request = [], None
		self.proof.is_new.return_value = True
		self.prepare = self.enterContext(
			patch(
				"vuna_transport.services.execution.transfer_proof_service.prepare_proof",
				return_value=self.proof,
			)
		)
		self.enterContext(
			patch(
				"vuna_transport.services.execution.transfer_execution_service._lock_task",
				return_value=frappe._dict(name="TASK", delivery_trip="TRIP", task_type="Transfer Receipt"),
			)
		)
		self.enterContext(
			patch.object(mobile, "_require_mobile_driver", return_value=frappe._dict(name="DRIVER"))
		)
		self.assigned = self.enterContext(patch.object(mobile, "_get_assigned_trip"))
		self.enterContext(patch.object(mobile, "_get_rejection_reasons", return_value=["Damage"]))

	def test_browsing_does_not_create_proof(self):
		result = prepare_mobile_transport_proof("DEVICE", "TASK")
		self.assertIsNone(result["name"])
		self.proof.insert.assert_not_called()
		self.proof.submit.assert_not_called()
		self.assertEqual(result["rejection_reasons"], ["Damage"])

	def test_evidence_upload_can_persist_only_a_draft(self):
		self.proof.is_new.side_effect = [True, False]
		result = prepare_mobile_transport_proof("DEVICE", "TASK", save_draft=True)
		self.assertEqual(result["name"], "POD")
		self.proof.insert.assert_called_once_with(ignore_permissions=True)
		self.proof.submit.assert_not_called()

	def test_existing_draft_or_submitted_proof_is_not_recreated(self):
		for docstatus in (0, 1):
			self.proof.docstatus = docstatus
			self.proof.is_new.return_value = False
			result = prepare_mobile_transport_proof("DEVICE", "TASK", save_draft=True)
			self.assertEqual(result["docstatus"], docstatus)
		self.proof.insert.assert_not_called()

	def test_unassigned_driver_cannot_create_evidence_draft(self):
		self.assigned.side_effect = PermissionError
		with self.assertRaises(PermissionError):
			prepare_mobile_transport_proof("DEVICE", "TASK", save_draft=True)
		self.prepare.assert_not_called()


class TestSharedPrivatePODUpload(TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(mobile, "frappe"))
		self.framework.throw.side_effect = ValueError
		self.framework.db.get_value.return_value = None
		self.proof = MagicMock(delivery_trip="TRIP", delivery_stop="STOP", docstatus=0)
		self.proof.name = "POD"
		self.framework.get_doc.return_value = self.proof
		self.enterContext(patch.object(mobile, "_", side_effect=lambda value: value))
		self.enterContext(
			patch.object(mobile, "_require_mobile_driver", return_value=frappe._dict(name="DRIVER"))
		)
		self.enterContext(patch.object(mobile, "_get_assigned_trip", return_value=frappe._dict(name="TRIP")))
		self.enterContext(patch.object(mobile, "_get_trip_stop"))
		self.save_file = self.enterContext(
			patch.object(mobile, "save_file", return_value=frappe._dict(file_url="/private/files/proof.svg"))
		)

	def upload(self):
		return mobile.upload_mobile_pod_attachment(
			"DEVICE", "TRIP", "STOP", "POD", "signature", "<svg/>", "proof.svg", content_encoding="text"
		)

	def test_task_proof_uses_same_private_field_attachment_as_customer_pod(self):
		self.assertEqual(self.upload()["file_url"], "/private/files/proof.svg")
		self.save_file.assert_called_once_with(
			"proof.svg", b"<svg/>", "Proof of Delivery", "POD", is_private=1, df="signature"
		)
		self.proof.db_set.assert_called_once_with(
			"signature", "/private/files/proof.svg", update_modified=False
		)

	def test_wrong_stop_and_submitted_proof_cannot_receive_attachments(self):
		for field, value in (("delivery_stop", "OTHER"), ("delivery_trip", "OTHER"), ("docstatus", 1)):
			with patch.object(self.proof, field, value), self.assertRaises(ValueError):
				self.upload()
		self.save_file.assert_not_called()

	def test_identical_upload_retry_reuses_existing_private_file(self):
		self.framework.db.get_value.return_value = frappe._dict(
			name="FILE", file_url="/private/files/existing.svg"
		)
		self.assertEqual(self.upload()["file_url"], "/private/files/existing.svg")
		self.save_file.assert_not_called()
