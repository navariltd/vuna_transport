# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.exceptions import ValidationError
from frappe.tests import UnitTestCase

from vuna_transport.services.execution.return_service import (
	RETURN_PHYSICAL_RESOLUTIONS,
	_return_method_from_custody,
	_validate_status_requirements,
	create_return_quality_inspections,
	create_return_request_from_exception,
	get_remaining_returnable_quantity,
)


class TestReturnRequest(UnitTestCase):
	def test_delivery_exception_link_is_unique(self):
		self.assertTrue(frappe.get_meta("Return Request").get_field("delivery_exception").unique)

	def test_exception_return_methods_follow_custody(self):
		self.assertEqual(_return_method_from_custody("Still With Vehicle"), "Driver Return")
		self.assertEqual(_return_method_from_custody("Left With Customer"), "Customer Drop-off")
		self.assertIn("Return to Nearest Depot", RETURN_PHYSICAL_RESOLUTIONS)

	@patch("vuna_transport.services.execution.return_service.require_document_permission")
	@patch("vuna_transport.services.execution.return_service.frappe.get_doc")
	@patch("vuna_transport.services.execution.return_service.frappe.db.sql")
	@patch("vuna_transport.services.execution.return_service.require_doctype_permission")
	def test_exception_return_request_creation_is_idempotent(
		self, require_doctype_permission, lock_exception, get_doc, require_document_permission
	):
		get_doc.return_value = SimpleNamespace(company="Test Company", return_request="RR-0001")
		self.assertEqual(create_return_request_from_exception("DEX-0001"), "RR-0001")
		lock_exception.assert_called_once()
		require_doctype_permission.assert_called_once_with("Return Request", "create")
		require_document_permission.assert_called_once_with(get_doc.return_value, "write")

	def test_received_quantity_must_be_inspected_before_resolution(self):
		request = SimpleNamespace(
			status="Awaiting Resolution",
			items=[
				SimpleNamespace(
					item_code="MILK", received_qty=10, accepted_return_qty=9, rejected_return_qty=0
				)
			],
		)
		with self.assertRaises(ValidationError):
			_validate_status_requirements(request)

	def test_resolution_requires_accepted_quantity_to_be_accounted_for(self):
		request = SimpleNamespace(
			status="Resolved",
			items=[
				SimpleNamespace(
					item_code="MILK",
					received_qty=10,
					accepted_return_qty=10,
					rejected_return_qty=0,
					resolution_type=None,
					resolution_qty=0,
				)
			],
		)
		with self.assertRaises(ValidationError):
			_validate_status_requirements(request)

	@patch("vuna_transport.services.execution.return_service.require_document_permission")
	@patch("vuna_transport.services.execution.return_service.frappe.get_doc")
	@patch("vuna_transport.services.execution.return_service.require_doctype_permission")
	def test_received_return_items_prepare_quality_inspections(
		self, require_doctype_permission, get_doc, require_document_permission
	):
		item = SimpleNamespace(
			item_code="MILK", received_qty=10, quality_inspection=None, batch_no=None, delivery_note_item="DNI-1"
		)
		request = SimpleNamespace(
			status="Received",
			company="Test Company",
			delivery_note="DN-0001",
			items=[item],
			append_activity=MagicMock(),
			save=MagicMock(),
		)
		inspection = SimpleNamespace(name="QI-0001", insert=MagicMock())
		get_doc.side_effect = lambda source, *args: inspection if isinstance(source, dict) else request

		self.assertEqual(create_return_quality_inspections("RR-0001"), ["QI-0001"])
		self.assertEqual(item.quality_inspection, "QI-0001")
		inspection.insert.assert_called_once()
		request.save.assert_called_once()
		require_doctype_permission.assert_called_once_with("Quality Inspection", "create")

	@patch("vuna_transport.services.execution.return_service.frappe.db.sql")
	@patch("vuna_transport.services.execution.return_service.frappe.db.get_value")
	def test_previous_returns_reduce_remaining_returnable_quantity(self, get_value, sql):
		get_value.return_value = {"parent": "DN-1", "stock_qty": 100, "qty": 100}
		sql.side_effect = [[(20,)], [(30,)]]
		self.assertEqual(get_remaining_returnable_quantity("DNI-1", "RR-2"), 50)

	def test_status_lifecycle_is_controlled(self):
		from vuna_transport.services.execution.return_service import STATUS_TRANSITIONS

		self.assertEqual(STATUS_TRANSITIONS["request"], ("Draft", "Requested"))
		self.assertEqual(STATUS_TRANSITIONS["close"], (("Resolved",), "Closed"))
		self.assertEqual(STATUS_TRANSITIONS["await_return"], (("Approved",), "Awaiting Return"))
		self.assertEqual(STATUS_TRANSITIONS["dispatch"], (("Awaiting Return",), "In Transit"))
		self.assertEqual(
			STATUS_TRANSITIONS["await_resolution"], (("Under Inspection",), "Awaiting Resolution")
		)
