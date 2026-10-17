# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.exceptions import ValidationError
from frappe.tests import UnitTestCase

from vuna_transport.services.execution.delivery_note_scan_service import (
	build_delivery_note_qr_token,
	scan_delivery_note_qr,
)


class TestDeliveryNoteScanService(UnitTestCase):
	def test_qr_token_is_signed_and_verifiable(self):
		with patch(
			"vuna_transport.services.execution.delivery_note_scan_service._secret",
			return_value="test-secret",
		):
			token = build_delivery_note_qr_token("DN-0001")

		self.assertTrue(token.startswith("VUNA-DN-1:DN-0001:"))
		self.assertEqual(len(token.rsplit(":", 1)[1]), 64)

	def test_qr_token_signature_changes_for_another_delivery_note(self):
		with patch(
			"vuna_transport.services.execution.delivery_note_scan_service._secret",
			return_value="test-secret",
		):
			first = build_delivery_note_qr_token("DN-0001")
			second = build_delivery_note_qr_token("DN-0002")

		self.assertNotEqual(first, second)

	@patch("vuna_transport.services.execution.delivery_note_scan_service.require_document_permission")
	@patch(
		"vuna_transport.services.execution.delivery_note_scan_service._resolve_qr_token",
		return_value=SimpleNamespace(name="DN-RETURN-1", is_return=1),
	)
	@patch("vuna_transport.services.execution.delivery_note_scan_service._require_scan_action_permission")
	def test_sales_return_cannot_enter_outbound_scan_workflow(
		self, _require_action, _resolve_token, _require_permission
	):
		with self.assertRaises(ValidationError):
			scan_delivery_note_qr("token", "Loaded")

		frappe.local.message_log = []
