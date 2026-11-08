from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.exceptions import ValidationError
from frappe.tests import UnitTestCase

from vuna_transport.services.execution.return_v2_service import (
	sync_return_quality_inspection,
	validate_return_inspection,
	validate_return_stock_entry,
	validate_sales_return_lineage,
)


class DocumentStub(SimpleNamespace):
	def get(self, fieldname, default=None):
		return getattr(self, fieldname, default)


class TestReturnV2Service(UnitTestCase):
	def test_sales_return_lineage_allows_split_rows(self):
		request = DocumentStub(
			name="RR-1",
			return_warehouse="Returns - TC",
			items=[
				frappe._dict(
					delivery_note_item="DNI-1",
					item_code="ITEM-1",
					requested_return_qty=15,
					conversion_factor=1,
				)
			],
		)
		sales_return = DocumentStub(
			items=[
				frappe._dict(idx=1, dn_detail="DNI-1", stock_qty=-7, warehouse="Returns - TC"),
				frappe._dict(idx=2, dn_detail="DNI-1", stock_qty=-3, warehouse="Returns - TC"),
				frappe._dict(idx=3, dn_detail="DNI-1", stock_qty=-5, warehouse="Returns - TC"),
			]
		)

		validate_sales_return_lineage(sales_return, request)

	def test_sales_return_lineage_rejects_wrong_warehouse(self):
		request = DocumentStub(
			return_warehouse="Returns - TC",
			items=[
				frappe._dict(
					delivery_note_item="DNI-1",
					item_code="ITEM-1",
					requested_return_qty=10,
					conversion_factor=1,
				)
			],
		)
		sales_return = DocumentStub(
			items=[frappe._dict(idx=1, dn_detail="DNI-1", stock_qty=-10, warehouse="Finished - TC")]
		)

		with self.assertRaises(ValidationError):
			validate_sales_return_lineage(sales_return, request)

	@patch("vuna_transport.services.execution.return_v2_service.frappe.get_doc")
	@patch(
		"vuna_transport.services.execution.return_v2_service.frappe.db.get_value",
		return_value="RIN-1",
	)
	def test_return_stock_entry_must_match_inspection(self, _get_value, get_doc):
		get_doc.return_value = DocumentStub(
			name="RIN-1",
			company="Test Company",
			source_warehouse="Returns - TC",
			results=[
				frappe._dict(
					item_code="ITEM-1",
					target_warehouse="Finished - TC",
					batch_no=None,
					inspection_qty=10,
				)
			],
		)
		stock_entry = DocumentStub(
			name="STE-1",
			company="Test Company",
			purpose="Material Transfer",
			items=[
				frappe._dict(
					idx=1,
					item_code="ITEM-1",
					s_warehouse="Returns - TC",
					t_warehouse="Finished - TC",
					batch_no=None,
					qty=9,
					transfer_qty=9,
				)
			],
		)

		with self.assertRaises(ValidationError):
			validate_return_stock_entry(stock_entry)

	@patch("vuna_transport.services.execution.return_v2_service.frappe.db.set_value")
	@patch("vuna_transport.services.execution.return_v2_service.frappe.get_all")
	def test_quality_inspection_backfills_direct_delivery_note_item(self, get_all, set_value):
		get_all.return_value = [frappe._dict(name="RIR-1"), frappe._dict(name="RIR-2")]
		quality_inspection = DocumentStub(
			name="QI-1",
			docstatus=0,
			reference_type="Delivery Note",
			child_row_reference="DNI-RETURN-1",
		)

		sync_return_quality_inspection(quality_inspection)

		get_all.assert_called_once_with(
			"Return Inspection Result",
			filters={"delivery_note_item": "DNI-RETURN-1", "docstatus": ["!=", 2]},
			fields=["name"],
		)
		self.assertEqual(set_value.call_count, 2)

	@patch("vuna_transport.services.execution.return_v2_service.validate_uom_is_integer")
	@patch("vuna_transport.services.execution.return_v2_service._validate_warehouse")
	@patch("vuna_transport.services.execution.return_v2_service._validate_rejected_warehouse")
	@patch("vuna_transport.services.execution.return_v2_service.require_document_permission")
	@patch("vuna_transport.services.execution.return_v2_service.frappe.get_doc")
	def test_inspection_preserves_multiple_rejection_reasons(
		self,
		get_doc,
		_require_document_permission,
		_validate_rejected_warehouse,
		_validate_warehouse,
		_validate_uom,
	):
		sales_return = DocumentStub(
			name="DN-RETURN-1",
			docstatus=1,
			is_return=1,
			vuna_return_request="RR-1",
			company="Test Company",
			customer="Test Customer",
			items=[
				frappe._dict(
					name="DNI-RETURN-1",
					item_code="ITEM-1",
					stock_qty=-20,
					stock_uom="Nos",
					warehouse="Returns - TC",
					batch_no=None,
					serial_no=None,
				)
			],
		)
		request = DocumentStub(name="RR-1")
		get_doc.side_effect = [sales_return, request]
		results = [
			frappe._dict(
				idx=1,
				delivery_note_item="DNI-RETURN-1",
				item_code="ITEM-1",
				inspection_qty=10,
				result="Accepted",
				reason=None,
				target_warehouse="Finished - TC",
				serial_no=None,
			),
			frappe._dict(
				idx=2,
				delivery_note_item="DNI-RETURN-1",
				item_code="ITEM-1",
				inspection_qty=4,
				result="Rejected",
				reason="Coagulation",
				target_warehouse="Rejected - TC",
				serial_no=None,
			),
			frappe._dict(
				idx=3,
				delivery_note_item="DNI-RETURN-1",
				item_code="ITEM-1",
				inspection_qty=6,
				result="Rejected",
				reason="Physical Damage",
				target_warehouse="Rejected - TC",
				serial_no=None,
			),
		]
		inspection = DocumentStub(
			sales_return="DN-RETURN-1",
			docstatus=1,
			accepted_warehouse="Finished - TC",
			rejected_warehouse="Rejected - TC",
			results=results,
		)

		validate_return_inspection(inspection)

		self.assertEqual(inspection.total_inspected_stock_qty, 20)
		self.assertEqual(inspection.accepted_stock_qty, 10)
		self.assertEqual(inspection.rejected_stock_qty, 10)
		self.assertEqual([row.reason for row in results[1:]], ["Coagulation", "Physical Damage"])

	@patch(
		"vuna_transport.services.execution.return_v2_service.frappe.db.get_single_value",
		return_value=1,
	)
	@patch("vuna_transport.services.execution.return_v2_service._validate_warehouse")
	@patch("vuna_transport.services.execution.return_v2_service._validate_rejected_warehouse")
	@patch("vuna_transport.services.execution.return_v2_service.require_document_permission")
	@patch("vuna_transport.services.execution.return_v2_service.frappe.get_doc")
	def test_enabled_quality_inspection_setting_requires_one_qi_per_result_group(
		self,
		get_doc,
		_require_document_permission,
		_validate_rejected_warehouse,
		_validate_warehouse,
		_get_single_value,
	):
		sales_return = DocumentStub(
			name="DN-RETURN-1",
			docstatus=1,
			is_return=1,
			vuna_return_request="RR-1",
			company="Test Company",
			customer="Test Customer",
			items=[
				frappe._dict(
					name="DNI-RETURN-1",
					item_code="ITEM-1",
					stock_qty=-20,
					stock_uom="Nos",
					warehouse="Returns - TC",
					batch_no=None,
					serial_no=None,
				)
			],
		)
		request = DocumentStub(name="RR-1")
		get_doc.side_effect = [sales_return, request]
		inspection = DocumentStub(
			sales_return="DN-RETURN-1",
			docstatus=1,
			accepted_warehouse="Finished - TC",
			rejected_warehouse="Rejected - TC",
			results=[
				frappe._dict(
					idx=1,
					delivery_note_item="DNI-RETURN-1",
					item_code="ITEM-1",
					inspection_qty=20,
					result="Accepted",
					target_warehouse="Finished - TC",
					quality_inspection=None,
					serial_no=None,
				)
			],
		)

		with self.assertRaisesRegex(ValidationError, "Quality Inspection is required"):
			validate_return_inspection(inspection)
