# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

from unittest.mock import patch

from frappe.tests import UnitTestCase

from vuna_transport.services.execution.delivery_note_scan_service import (
	build_delivery_note_qr_token,
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
