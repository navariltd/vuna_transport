# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests import UnitTestCase

from vuna_transport.services.execution.lineage_service import (
	complete_load_without_trip,
	populate_delivery_note_delivery_links,
	reconcile_load_after_delivery_note_cancel,
)


class TestDeliveryNoteReturnLineage(UnitTestCase):
	@patch("vuna_transport.services.execution.lineage_service.get_locked_delivery_load")
	def test_sales_return_skips_forward_load_validation(self, get_locked_delivery_load):
		populate_delivery_note_delivery_links(SimpleNamespace(is_return=1, vuna_delivery_load="DL-0001"))

		get_locked_delivery_load.assert_not_called()

	@patch("vuna_transport.services.execution.lineage_service.frappe.get_doc")
	def test_sales_return_does_not_complete_delivery_load(self, get_doc):
		complete_load_without_trip(
			SimpleNamespace(
				is_return=1,
				docstatus=1,
				vuna_delivery_load="DL-0001",
			)
		)

		get_doc.assert_not_called()

	@patch("vuna_transport.services.execution.lineage_service.get_locked_delivery_load")
	def test_cancelled_sales_return_does_not_reopen_delivery_load(self, get_locked_delivery_load):
		reconcile_load_after_delivery_note_cancel(SimpleNamespace(is_return=1, vuna_delivery_load="DL-0001"))

		get_locked_delivery_load.assert_not_called()
