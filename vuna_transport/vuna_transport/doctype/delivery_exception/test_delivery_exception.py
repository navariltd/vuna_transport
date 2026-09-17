# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.exceptions import ValidationError
from frappe.tests import UnitTestCase

from vuna_transport.services.execution.delivery_exception_service import (
	POD_EXCEPTION_TYPES,
	_validate_erpnext_links,
	_validate_follow_up_delivery,
	_validate_resolution,
	_validate_resolution_plan,
	validate_exception_cancellation,
	_validate_return_quantities,
	get_exception_item_values,
	sync_delivery_exception_from_pod,
)


class TestDeliveryException(UnitTestCase):
	def test_delivery_exception_is_submittable(self):
		self.assertTrue(frappe.get_meta("Delivery Exception").is_submittable)

	@patch("vuna_transport.services.execution.delivery_exception_service.frappe.db.get_value")
	def test_cancellation_requires_downstream_return_request_to_be_cancelled(self, get_value):
		get_value.return_value = 0
		exception = SimpleNamespace(
			name="DEX-1",
			return_request="RR-1",
			return_document=None,
			credit_note=None,
			replacement_delivery_reference=None,
		)
		with self.assertRaises(ValidationError):
			validate_exception_cancellation(exception)

	def test_resolution_plan_supports_mixed_item_outcomes(self):
		exception = self._exception()
		exception.resolutions = [
			SimpleNamespace(
				idx=1,
				delivery_note_item="DNI-1",
				item_code="MILK",
				quantity=10,
				custody="Returned To Depot",
				responsible_party="Company",
				physical_resolution="Return to Nearest Depot",
				commercial_resolution="Credit Note",
				resolution_warehouse="NEAREST - VF",
			),
			SimpleNamespace(
				idx=2,
				delivery_note_item="DNI-1",
				item_code="MILK",
				quantity=20,
				custody="Left With Customer",
				responsible_party="Customer",
				physical_resolution="Leave With Customer",
				commercial_resolution="Replacement",
				resolution_warehouse=None,
			),
		]

		self.assertEqual(_validate_resolution_plan(exception, require_complete=True), {"DNI-1": 30})
	def test_proof_of_delivery_is_unique_exception_context(self):
		self.assertTrue(frappe.get_meta("Delivery Exception").get_field("proof_of_delivery").unique)

	def test_partial_rejected_and_failed_pods_have_exception_types(self):
		self.assertEqual(POD_EXCEPTION_TYPES["Partially Delivered"], "Partial Delivery")
		self.assertEqual(POD_EXCEPTION_TYPES["Rejected"], "Rejected Delivery")
		self.assertEqual(POD_EXCEPTION_TYPES["Failed"], "Failed Delivery")

	@patch("vuna_transport.services.execution.delivery_exception_service.populate_exception_from_pod")
	@patch("vuna_transport.services.execution.delivery_exception_service.frappe.new_doc")
	@patch("vuna_transport.services.execution.delivery_exception_service.frappe.db.get_value")
	def test_non_accepted_pods_create_exceptions(self, get_value, new_doc, populate):
		for status in ("Partially Delivered", "Rejected", "Failed"):
			get_value.side_effect = [None, None]
			exception = MagicMock(name=f"DEX-{status}")
			exception.name = f"DEX-{status}"
			new_doc.return_value = exception
			pod = SimpleNamespace(
				status=status,
				name=f"POD-{status}",
				delivery_trip="DT-1",
				delivery_stop="STOP-1",
			)
			self.assertEqual(sync_delivery_exception_from_pod(pod), exception.name)
			exception.insert.assert_called_once()
			populate.assert_called_with(exception, pod)
			exception.reset_mock()

	@patch("vuna_transport.services.execution.delivery_exception_service.frappe.db.get_value")
	def test_duplicate_pod_handling_is_idempotent(self, get_value):
		get_value.return_value = "DEX-1"
		pod = SimpleNamespace(status="Rejected", name="POD-1")
		self.assertEqual(sync_delivery_exception_from_pod(pod), "DEX-1")

	@patch("vuna_transport.services.execution.delivery_exception_service.frappe.db.get_value")
	def test_accepted_pod_creates_no_exception(self, get_value):
		pod = SimpleNamespace(status="Accepted", name="POD-1")
		self.assertIsNone(sync_delivery_exception_from_pod(pod))
		get_value.assert_not_called()

	def test_unresolved_quantity_preserves_original_outcome(self):
		values = get_exception_item_values(
			SimpleNamespace(
				item_code="MILK",
				item_name="Milk",
				delivery_order_item="DOI-1",
				delivery_note_item="DNI-1",
				ordered_qty=100,
				delivered_qty=70,
				rejected_qty=20,
			),
			"Nos",
		)
		self.assertEqual(values["planned_qty"], 100)
		self.assertEqual(values["delivered_qty"], 70)
		self.assertEqual(values["unresolved_qty"], 30)

	def test_goods_with_unknown_custody_cannot_enter_resolution(self):
		exception = self._exception()
		exception.custody = "Unknown"
		with self.assertRaises(ValidationError):
			_validate_resolution(exception)

	def test_goods_left_with_customer_are_not_immediately_redeliverable(self):
		exception = self._exception()
		exception.resolution_status = "Open"
		exception.custody = "Left With Customer"
		exception.replacement_delivery_reference = "DO-FOLLOW-UP"
		with self.assertRaises(ValidationError):
			_validate_follow_up_delivery(exception)

	def test_nearest_depot_can_differ_from_original_source(self):
		exception = self._exception()
		exception.physical_resolution = "Return to Nearest Depot"
		exception.resolution_warehouse = "NEAREST - VF"
		_validate_resolution(exception)

	@patch("vuna_transport.services.execution.delivery_exception_service.frappe.get_doc")
	def test_return_resolution_links_stock_return(self, get_doc):
		exception = self._exception()
		exception.return_document = "DN-RETURN-1"
		get_doc.return_value = SimpleNamespace(
			name="DN-RETURN-1",
			is_return=1,
			return_against="DN-1",
			company="Vuna Foods",
			customer="CUSTOMER-1",
			docstatus=0,
		)
		_validate_erpnext_links(exception)

	def test_return_quantity_cannot_exceed_exception_quantity(self):
		exception = self._exception()
		exception.physical_resolution = "Return to Nearest Depot"
		return_document = SimpleNamespace(items=[SimpleNamespace(dn_detail="DNI-1", stock_qty=-31, qty=-31)])
		with self.assertRaises(ValidationError):
			_validate_return_quantities(exception, return_document)

	@patch("vuna_transport.services.execution.delivery_exception_service.frappe.get_doc")
	def test_credit_resolution_links_credit_note(self, get_doc):
		exception = self._exception()
		exception.credit_note = "SINV-RETURN-1"
		get_doc.return_value = SimpleNamespace(
			name="SINV-RETURN-1",
			is_return=1,
			company="Vuna Foods",
			customer="CUSTOMER-1",
			docstatus=0,
		)
		_validate_erpnext_links(exception)

	@patch("vuna_transport.services.execution.delivery_exception_service.frappe.get_doc")
	def test_follow_up_cannot_exceed_unresolved_resolution_quantity(self, get_doc):
		exception = self._exception()
		exception.commercial_resolution = "Re-delivery"
		exception.replacement_delivery_reference = "DO-FOLLOW-UP"
		get_doc.return_value = SimpleNamespace(
			name="DO-FOLLOW-UP",
			company="Vuna Foods",
			customer="CUSTOMER-1",
			docstatus=0,
			source_warehouse="NEAREST - VF",
			items=[SimpleNamespace(item_code="MILK", transport_qty=31)],
		)
		with self.assertRaises(ValidationError):
			_validate_follow_up_delivery(exception)

	@patch("vuna_transport.services.execution.delivery_exception_service.frappe.get_doc")
	def test_replacement_preserves_original_delivery_reference(self, get_doc):
		exception = self._exception()
		exception.commercial_resolution = "Replacement"
		exception.replacement_delivery_reference = "DO-FOLLOW-UP"
		get_doc.return_value = SimpleNamespace(
			name="DO-FOLLOW-UP",
			company="Vuna Foods",
			customer="CUSTOMER-1",
			docstatus=0,
			source_warehouse="NEAREST - VF",
			items=[SimpleNamespace(item_code="MILK", transport_qty=30)],
		)
		_validate_follow_up_delivery(exception)
		self.assertEqual(exception.delivery_order, "DO-ORIGINAL")

	@staticmethod
	def _exception():
		return SimpleNamespace(
			name="DEX-1",
			company="Vuna Foods",
			customer="CUSTOMER-1",
			delivery_order="DO-ORIGINAL",
			delivery_note="DN-1",
			resolution_status="In Resolution",
			custody="Returned To Depot",
			responsible_party="Company",
			responsible_user="Administrator",
			physical_resolution="Re-deliver",
			commercial_resolution="Re-delivery",
			resolution_warehouse="NEAREST - VF",
			resolved_on=None,
			return_document=None,
			credit_note=None,
			replacement_delivery_reference=None,
			items=[
				SimpleNamespace(
					item_code="MILK",
					delivery_note_item="DNI-1",
					unresolved_qty=30,
					resolution_qty=30,
					custody=None,
					responsible_party=None,
					physical_resolution=None,
					commercial_resolution=None,
				)
			],
		)
