# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.exceptions import ValidationError
from frappe.tests import UnitTestCase

from vuna_transport.services.execution.return_service import (
	_get_pod_item_return_reason,
	_get_sales_return_outcomes,
	_map_split_credit_note_rows,
	_map_split_sales_return_rows,
	create_return_request_from_pod,
	get_pod_quantity_in_sales_uom,
	get_remaining_returnable_quantity,
	get_return_request_state,
	get_sales_invoice_return_context,
	prepare_sales_return_cancellation,
	sync_return_request_from_commercial_document,
	sync_return_request_from_sales_return,
	sync_sales_return_request_link,
	validate_linked_sales_return_submission,
	validate_sales_return_request_link,
)


class RequestStub(SimpleNamespace):
	def get(self, fieldname, default=None):
		return getattr(self, fieldname, default)


def make_request(**values):
	request = RequestStub(
		name="RR-0001",
		docstatus=1,
		status="Awaiting Sales Return",
		return_stock_document=None,
		no_further_action=0,
		credit_note=None,
		replacement_sales_order=None,
		refund_payment_entry=None,
		replacement_delivery_reference=None,
		items=[],
	)
	for fieldname, value in values.items():
		setattr(request, fieldname, value)
	return request


class TestReturnRequest(UnitTestCase):
	def test_return_request_is_submittable(self):
		self.assertTrue(frappe.get_meta("Return Request").is_submittable)

	@patch("vuna_transport.vuna_transport.doctype.return_request.return_request.frappe.db.set_value")
	@patch(
		"vuna_transport.vuna_transport.doctype.return_request.return_request.frappe.db.get_value",
		return_value="RR-0001",
	)
	def test_deleting_request_clears_pod_backlink(self, get_value, set_value):
		from vuna_transport.vuna_transport.doctype.return_request.return_request import ReturnRequest

		request = SimpleNamespace(name="RR-0001", proof_of_delivery="POD-0001")
		ReturnRequest.unlink_proof_of_delivery(request)

		get_value.assert_called_once_with("Proof of Delivery", "POD-0001", "return_request")
		set_value.assert_called_once_with(
			"Proof of Delivery", "POD-0001", "return_request", None, update_modified=False
		)

	@patch("vuna_transport.vuna_transport.doctype.return_request.return_request.frappe.db.set_value")
	@patch(
		"vuna_transport.vuna_transport.doctype.return_request.return_request.frappe.db.get_value",
		return_value="RR-NEWER",
	)
	def test_deleting_old_request_preserves_newer_pod_backlink(self, _get_value, set_value):
		from vuna_transport.vuna_transport.doctype.return_request.return_request import ReturnRequest

		request = SimpleNamespace(name="RR-OLD", proof_of_delivery="POD-0001")
		ReturnRequest.unlink_proof_of_delivery(request)

		set_value.assert_not_called()

	def test_pod_item_rejection_reason_becomes_return_reason(self):
		pod = SimpleNamespace(rejection_reasons=[], remarks="General remarks", notes=None)
		item = SimpleNamespace(
			rejection_reason="Damaged",
			rejection_reason_remarks="Outer carton was wet",
		)

		self.assertEqual(_get_pod_item_return_reason(pod, item), "Damaged")

	@patch("vuna_transport.services.execution.return_service.require_company_access")
	@patch("vuna_transport.services.execution.return_service.require_document_permission")
	@patch("vuna_transport.services.execution.return_service.require_doctype_permission")
	@patch("vuna_transport.services.execution.return_service.frappe.get_doc")
	def test_invoice_with_multiple_delivery_notes_returns_a_selection_context(
		self, get_doc, _require_doctype_permission, _require_document_permission, _require_company_access
	):
		get_doc.return_value = SimpleNamespace(
			name="SINV-1",
			company="Test Company",
			docstatus=1,
			is_return=0,
			update_stock=0,
			items=[SimpleNamespace(delivery_note="DN-2"), SimpleNamespace(delivery_note="DN-1")],
		)

		context = get_sales_invoice_return_context("SINV-1")

		self.assertIsNone(context["delivery_note"])
		self.assertEqual(context["delivery_notes"], ["DN-1", "DN-2"])

	def test_pod_form_rejection_reasons_are_return_reason_fallback(self):
		pod = SimpleNamespace(
			rejection_reasons=[
				SimpleNamespace(rejection_reason="Customer Refused"),
				SimpleNamespace(rejection_reason="Product Damaged"),
			],
			remarks="General remarks",
			notes=None,
		)
		item = SimpleNamespace(rejection_reason=None, rejection_reason_remarks=None)

		self.assertEqual(
			_get_pod_item_return_reason(pod, item),
			"Customer Refused, Product Damaged",
		)

	@patch("vuna_transport.services.execution.return_service.frappe.db.get_value", return_value="RR-0001")
	@patch("vuna_transport.services.execution.return_service.frappe.db.sql")
	@patch("vuna_transport.services.execution.return_service.frappe.get_doc")
	@patch("vuna_transport.services.execution.return_service.require_document_permission")
	@patch("vuna_transport.services.execution.return_service.require_doctype_permission")
	def test_pod_return_request_creation_is_idempotent(
		self, require_doctype_permission, require_document_permission, get_doc, lock_pod, get_value
	):
		get_doc.return_value = frappe._dict(
			name="POD-0001", company="Test Company", docstatus=1, status="Rejected"
		)
		self.assertEqual(create_return_request_from_pod("POD-0001"), "RR-0001")
		lock_pod.assert_called_once()
		require_doctype_permission.assert_called_once_with("Return Request", "create")

	@patch("vuna_transport.services.execution.return_service.frappe.db.get_value")
	def test_only_submitted_returns_reduce_returnable_quantity(self, get_value):
		get_value.return_value = {
			"parent": "DN-1",
			"stock_qty": 100,
			"qty": 10,
			"conversion_factor": 10,
		}
		get_value.side_effect = (
			lambda doctype, *args, **kwargs: None if doctype == "Return Request" else get_value.return_value
		)
		query_type = type(frappe.qb.from_(frappe.qb.DocType("Delivery Note")))
		with (
			patch.object(
				query_type,
				"run",
				autospec=True,
				side_effect=[
					[frappe._dict(parent="SR-1", stock_qty=-20)],
					[frappe._dict(parent="RR-1", requested_return_qty=3, conversion_factor=10)],
				],
			) as run,
			patch("vuna_transport.services.execution.return_service.frappe.get_all", return_value=[]),
		):
			self.assertEqual(get_remaining_returnable_quantity("DNI-1", "RR-2"), 5)
			self.assertIn("`tabDelivery Note`.`docstatus`=1", run.call_args_list[0].args[0].get_sql())

	def test_pod_quantity_remains_in_sales_uom_for_return_request(self):
		pod_item = SimpleNamespace(uom="Carton", rejected_qty=2)
		delivery_note_item = SimpleNamespace(conversion_factor=12)

		self.assertEqual(
			get_pod_quantity_in_sales_uom(pod_item, delivery_note_item, "rejected_qty"),
			2,
		)

	def test_legacy_pod_stock_quantity_converts_to_sales_uom_for_return_request(self):
		pod_item = SimpleNamespace(uom=None, rejected_qty=24)
		delivery_note_item = SimpleNamespace(conversion_factor=12)

		self.assertEqual(
			get_pod_quantity_in_sales_uom(pod_item, delivery_note_item, "rejected_qty"),
			2,
		)

	def test_submitted_request_without_sales_return_awaits_sales_return(self):
		request = make_request(
			items=[
				SimpleNamespace(
					delivery_note_item="DNI-1",
					requested_return_qty=10,
					quality_inspection=None,
					accepted_return_qty=0,
					rejected_return_qty=0,
				)
			]
		)
		state = get_return_request_state(request)
		self.assertEqual(state["status"], "Awaiting Sales Return")
		self.assertEqual(state["expected_return_qty"], 10)
		self.assertEqual(state["returned_qty"], 0)

	@patch("vuna_transport.services.execution.return_service.frappe.get_all")
	@patch("vuna_transport.services.execution.return_service.frappe.db.get_value")
	@patch("vuna_transport.services.execution.return_service.frappe.db.exists", return_value=True)
	def test_draft_sales_return_does_not_require_pre_receipt_inspection(self, exists, get_value, get_all):
		get_value.return_value = 0
		get_all.return_value = []
		request = make_request(
			return_stock_document="DN-RET-1",
			items=[
				SimpleNamespace(
					delivery_note_item="DNI-1",
					requested_return_qty=10,
					quality_inspection=None,
					accepted_return_qty=0,
					rejected_return_qty=0,
				)
			],
		)
		self.assertEqual(get_return_request_state(request)["status"], "Awaiting Return Submission")

	@patch("vuna_transport.services.execution.return_service.frappe.get_all")
	@patch("vuna_transport.services.execution.return_service.frappe.db.get_value")
	@patch("vuna_transport.services.execution.return_service.frappe.db.exists", return_value=True)
	def test_submitted_sales_return_is_actual_stock_return(self, exists, get_value, get_all):
		get_value.return_value = 1
		get_all.return_value = [frappe._dict(dn_detail="DNI-1", stock_qty=-8, qty=-8)]
		request = make_request(
			return_stock_document="DN-RET-1",
			items=[
				SimpleNamespace(
					delivery_note_item="DNI-1",
					requested_return_qty=10,
					quality_inspection=None,
					accepted_return_qty=0,
					rejected_return_qty=0,
				)
			],
		)
		state = get_return_request_state(request)
		self.assertEqual(state["status"], "Awaiting Commercial Resolution")
		self.assertEqual(state["returned_qty"], 8)
		self.assertEqual(state["return_difference_qty"], 2)

	@patch("vuna_transport.services.execution.return_service.frappe.get_all")
	@patch("vuna_transport.services.execution.return_service.frappe.db.get_value")
	@patch("vuna_transport.services.execution.return_service.frappe.db.exists", return_value=True)
	def test_submitted_sales_return_is_reported_in_sales_uom(self, exists, get_value, get_all):
		get_value.return_value = 1
		get_all.return_value = [frappe._dict(dn_detail="DNI-1", stock_qty=-24, qty=-2)]
		request = make_request(
			return_stock_document="DN-RET-1",
			items=[
				SimpleNamespace(
					delivery_note_item="DNI-1",
					requested_return_qty=3,
					conversion_factor=12,
					quality_inspection=None,
					accepted_return_qty=0,
					rejected_return_qty=0,
				)
			],
		)

		state = get_return_request_state(request)
		self.assertEqual(state["returned_qty"], 2)
		self.assertEqual(state["returned_stock_by_item"]["DNI-1"], 24)
		self.assertEqual(state["return_difference_qty"], 1)

	@patch("vuna_transport.services.execution.return_service.frappe.get_all")
	@patch("vuna_transport.services.execution.return_service.frappe.db.get_value")
	@patch("vuna_transport.services.execution.return_service.frappe.db.exists", return_value=True)
	def test_submitted_credit_note_resolves_return(self, exists, get_value, get_all):
		def get_status(doctype, name, fieldname, *args, **kwargs):
			return 1

		get_value.side_effect = get_status
		get_all.return_value = [frappe._dict(dn_detail="DNI-1", stock_qty=-10, qty=-10)]
		request = make_request(
			return_stock_document="DN-RET-1",
			credit_note="SINV-RET-1",
			items=[
				SimpleNamespace(
					delivery_note_item="DNI-1",
					requested_return_qty=10,
					quality_inspection=None,
					accepted_return_qty=0,
					rejected_return_qty=0,
				)
			],
		)
		self.assertEqual(get_return_request_state(request)["status"], "Resolved")

	@patch("vuna_transport.services.execution.return_service.frappe.get_all")
	@patch("vuna_transport.services.execution.return_service.frappe.db.get_value", return_value=1)
	def test_direct_stock_invoice_credit_note_resolves_return(self, _get_value, get_all):
		get_all.return_value = [frappe._dict(sales_invoice_item="SII-1", stock_qty=-24, qty=-2)]
		request = make_request(
			delivery_note=None,
			credit_note="SINV-RET-1",
			items=[
				SimpleNamespace(
					sales_invoice_item="SII-1",
					requested_return_qty=2,
					conversion_factor=12,
					quality_inspection=None,
					accepted_return_qty=0,
					rejected_return_qty=0,
				)
			],
		)

		state = get_return_request_state(request)

		self.assertEqual(state["status"], "Resolved")
		self.assertEqual(state["returned_qty"], 2)
		self.assertEqual(state["returned_stock_by_item"]["SII-1"], 24)

	@patch("vuna_transport.services.execution.return_service.require_company_access")
	@patch("vuna_transport.services.execution.return_service.frappe.db.get_value")
	@patch("vuna_transport.services.execution.return_service.frappe.get_doc")
	def test_sales_return_cannot_exceed_requested_return_quantity(
		self, get_doc, get_value, require_company_access
	):
		request = make_request(
			company="Test Company",
			items=[
				SimpleNamespace(
					item_code="ITEM-1",
					delivery_note_item="DNI-1",
					requested_return_qty=10,
					accepted_return_qty=8,
					rejected_return_qty=2,
					quality_inspection="QI-1",
				)
			],
			return_warehouse="Returns - TC",
		)
		get_value.return_value = "RR-0001"
		get_doc.return_value = request
		doc = SimpleNamespace(
			name="DN-RET-1",
			is_return=1,
			items=[
				SimpleNamespace(
					idx=1,
					item_code="ITEM-1",
					dn_detail="DNI-1",
					stock_qty=-9,
					qty=-9,
					warehouse="Returns - TC",
				),
				SimpleNamespace(
					idx=2,
					item_code="ITEM-1",
					dn_detail="DNI-1",
					stock_qty=-2,
					qty=-2,
					warehouse="Rejected - TC",
				),
			],
		)
		with (
			patch(
				"vuna_transport.services.execution.return_service.frappe.get_all",
				return_value=["Rejected - TC"],
			),
			self.assertRaises(ValidationError),
		):
			validate_linked_sales_return_submission(doc)

	@patch(
		"vuna_transport.services.execution.return_service.frappe.get_all",
		return_value=["Rejected - TC"],
	)
	def test_sales_return_warehouses_define_accepted_and_rejected_quantities(self, _get_all):
		request = make_request(
			items=[SimpleNamespace(delivery_note_item="DNI-1", requested_return_qty=10, conversion_factor=1)]
		)
		sales_return = SimpleNamespace(
			items=[
				SimpleNamespace(dn_detail="DNI-1", qty=-7, stock_qty=-7, warehouse="Good Returns - TC"),
				SimpleNamespace(dn_detail="DNI-1", qty=-3, stock_qty=-3, warehouse="Rejected - TC"),
			]
		)

		outcome = _get_sales_return_outcomes(request, sales_return)["DNI-1"]

		self.assertEqual(outcome["returned"], 10)
		self.assertEqual(outcome["accepted"], 7)
		self.assertEqual(outcome["rejected"], 3)

	@patch(
		"vuna_transport.services.execution.return_service.frappe.get_all",
		return_value=["Rejected - TC"],
	)
	def test_sales_return_split_uoms_are_compared_in_request_uom(self, _get_all):
		request = make_request(
			items=[
				SimpleNamespace(
					delivery_note_item="DNI-1",
					requested_return_qty=20.833333333,
					conversion_factor=24,
				)
			]
		)
		sales_return = SimpleNamespace(
			items=[
				SimpleNamespace(dn_detail="DNI-1", qty=-200, stock_qty=-200, warehouse="Good Returns - TC"),
				SimpleNamespace(dn_detail="DNI-1", qty=-300, stock_qty=-300, warehouse="Rejected - TC"),
			]
		)

		outcome = _get_sales_return_outcomes(request, sales_return)["DNI-1"]

		self.assertAlmostEqual(outcome["returned"], 20.833333333)
		self.assertAlmostEqual(outcome["accepted"], 8.333333333)
		self.assertEqual(outcome["rejected"], 12.5)

	@patch(
		"vuna_transport.services.execution.return_service.frappe.db.get_value",
		return_value=frappe._dict(
			against_sales_order="SO-1",
			so_detail="SOI-1",
			price_list_rate=565,
			discount_percentage=3,
			discount_amount=16.95,
			rate=548.05,
		),
	)
	def test_split_sales_return_row_recovers_source_lineage_and_commercial_terms(self, _get_value):
		request = make_request(items=[SimpleNamespace(delivery_note_item="DNI-1", item_code="ITEM-1")])
		row = SimpleNamespace(
			idx=2,
			item_code="ITEM-1",
			dn_detail=None,
			against_sales_order=None,
			so_detail=None,
		)

		_map_split_sales_return_rows(request, SimpleNamespace(items=[row]))

		self.assertEqual(row.dn_detail, "DNI-1")
		self.assertEqual(row.against_sales_order, "SO-1")
		self.assertEqual(row.so_detail, "SOI-1")
		self.assertEqual(row.price_list_rate, 565)
		self.assertEqual(row.discount_percentage, 3)
		self.assertEqual(row.discount_amount, 16.95)
		self.assertEqual(row.rate, 548.05)

	@patch(
		"vuna_transport.services.execution.return_service.frappe.db.get_value",
		return_value=frappe._dict(price_list_rate=565, discount_percentage=3, rate=548.05),
	)
	def test_split_credit_note_row_recovers_invoice_lineage_and_commercial_terms(self, _get_value):
		request = make_request(items=[SimpleNamespace(sales_invoice_item="SII-1", item_code="ITEM-1")])
		row = SimpleNamespace(idx=2, item_code="ITEM-1", sales_invoice_item=None)

		_map_split_credit_note_rows(request, SimpleNamespace(items=[row]))

		self.assertEqual(row.sales_invoice_item, "SII-1")
		self.assertEqual(row.price_list_rate, 565)
		self.assertEqual(row.discount_percentage, 3)
		self.assertEqual(row.rate, 548.05)

	@patch(
		"vuna_transport.services.execution.return_service.frappe.db.get_value",
		return_value=None,
	)
	def test_sales_return_clears_outbound_dispatch_fields(self, _get_value):
		doc = SimpleNamespace(
			name="DN-RETURN-1",
			is_return=1,
			vuna_return_request=None,
			vuna_delivery_load="DL-1",
			vuna_delivery_order="DO-1",
			vuna_dispatch_status="Ready for Loading",
			vuna_dispatch_scan_count=2,
		)

		validate_sales_return_request_link(doc)

		self.assertIsNone(doc.vuna_delivery_load)
		self.assertIsNone(doc.vuna_delivery_order)
		self.assertIsNone(doc.vuna_dispatch_status)
		self.assertEqual(doc.vuna_dispatch_scan_count, 0)

	@patch("vuna_transport.services.execution.return_service.recalculate_return_request")
	@patch("vuna_transport.services.execution.return_service.frappe.db.set_value")
	@patch(
		"vuna_transport.services.execution.return_service.frappe.get_all",
		return_value=["Rejected - TC"],
	)
	@patch("vuna_transport.services.execution.return_service.frappe.get_doc")
	@patch(
		"vuna_transport.services.execution.return_service.frappe.db.get_value",
		return_value="RR-0001",
	)
	def test_submitted_sales_return_backfills_return_request_outcomes(
		self, _get_value, get_doc, _get_all, set_value, recalculate
	):
		get_doc.return_value = make_request(
			items=[SimpleNamespace(name="RRI-1", delivery_note_item="DNI-1", requested_return_qty=10)]
		)
		sales_return = SimpleNamespace(
			name="DN-RET-1",
			is_return=1,
			docstatus=1,
			items=[
				SimpleNamespace(dn_detail="DNI-1", qty=-7, warehouse="Good Returns - TC"),
				SimpleNamespace(dn_detail="DNI-1", qty=-3, warehouse="Rejected - TC"),
			],
		)

		sync_return_request_from_sales_return(sales_return)

		set_value.assert_called_once_with(
			"Return Request Item",
			"RRI-1",
			{
				"received_qty": 10,
				"accepted_return_qty": 7,
				"rejected_return_qty": 3,
				"remaining_qty": 0,
			},
			update_modified=False,
		)
		recalculate.assert_called_once_with("RR-0001")

	@patch("vuna_transport.services.execution.return_service.recalculate_return_request")
	@patch("vuna_transport.services.execution.return_service.frappe.db.set_value")
	@patch(
		"vuna_transport.services.execution.return_service.frappe.db.get_value",
		return_value=None,
	)
	@patch("vuna_transport.services.execution.return_service.frappe.get_all", return_value=[])
	def test_sales_return_form_link_backfills_return_request(
		self, _get_all, _get_value, set_value, recalculate
	):
		sync_sales_return_request_link(
			SimpleNamespace(name="DN-RET-1", is_return=1, vuna_return_request="RR-0001")
		)

		set_value.assert_called_once_with(
			"Return Request",
			"RR-0001",
			"return_stock_document",
			"DN-RET-1",
			update_modified=False,
		)
		recalculate.assert_called_once_with("RR-0001")

	@patch("vuna_transport.services.execution.return_service.recalculate_return_request")
	@patch("vuna_transport.services.execution.return_service.frappe.db.set_value")
	@patch(
		"vuna_transport.services.execution.return_service.frappe.db.get_value",
		side_effect=[None, "DN-RET-1"],
	)
	@patch(
		"vuna_transport.services.execution.return_service._get_linked_return_request_name",
		return_value="RR-0001",
	)
	def test_sales_return_cancellation_releases_return_request_reverse_link(
		self, _get_link, _get_value, set_value, recalculate
	):
		prepare_sales_return_cancellation(SimpleNamespace(name="DN-RET-1", is_return=1))

		set_value.assert_called_once_with(
			"Return Request",
			"RR-0001",
			"return_stock_document",
			None,
			update_modified=False,
		)
		recalculate.assert_called_once_with("RR-0001")

	@patch("vuna_transport.services.execution.return_service.recalculate_return_request")
	@patch("vuna_transport.services.execution.return_service.frappe.db.set_value")
	@patch("vuna_transport.services.execution.return_service.frappe.get_all", return_value=["RR-0001"])
	def test_standard_credit_note_from_sales_return_links_return_request(
		self, get_all, set_value, recalculate
	):
		credit_note = SimpleNamespace(
			doctype="Sales Invoice",
			name="SINV-RET-1",
			docstatus=1,
			is_return=1,
			company="Test Company",
			customer="Test Customer",
			items=[SimpleNamespace(delivery_note="DN-RET-1")],
		)

		sync_return_request_from_commercial_document(credit_note)

		get_all.assert_called_once_with(
			"Return Request",
			filters={
				"return_stock_document": ["in", ["DN-RET-1"]],
				"docstatus": ["!=", 2],
				"company": "Test Company",
				"customer": "Test Customer",
			},
			pluck="name",
		)
		set_value.assert_called_once_with(
			"Return Request",
			"RR-0001",
			{"credit_note": "SINV-RET-1", "commercial_resolution": "Credit Customer"},
			update_modified=True,
		)
		recalculate.assert_called_once_with("RR-0001")
