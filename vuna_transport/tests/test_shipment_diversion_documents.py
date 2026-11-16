"""Diversion document boundaries: approved quantity, custody, quality and retries."""

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from vuna_transport.services.execution import shipment_diversion_documents as service
from vuna_transport.tests.test_shipment_diversion_service import Document as BaseDocument


class Document(BaseDocument):
	def append(self, field, values):
		row = values if isinstance(values, BaseDocument) else Document(**values)
		getattr(self, field).append(row)
		return row


class TestDiversionDocuments(TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(service, "frappe"))
		self.framework._dict = frappe._dict
		self.framework.throw.side_effect = lambda message, *a, **kw: self.fail_rule(message)
		self.enterContext(patch.object(service, "_", side_effect=lambda message: message))
		self.enterContext(patch.object(service, "require_document_permission"))
		self.permission = self.enterContext(patch.object(service, "require_doctype_permission"))
		self.planned = frappe._dict(
			name="SDI",
			item_code="Milk",
			qty=2,
			stock_qty=48,
			uom="Carton",
			stock_uom="Pcs",
			conversion_factor=24,
			batch_no="B1",
			serial_no="",
			delivery_note_item="DNI",
		)
		self.doc = Document(
			name="SD",
			docstatus=1,
			company="Company",
			destination_type="Customer",
			return_request="RR",
			original_customer="A",
			customer="B",
			delivery_address="Address B",
			delivery_note="DN",
			vehicle_warehouse="Vehicle",
			destination_warehouse="Depot",
			items=[self.planned],
			db_set=MagicMock(),
		)
		self.row = frappe._dict(
			item_code="Milk",
			qty=-2,
			stock_qty=-48,
			uom="Carton",
			stock_uom="Pcs",
			conversion_factor=24,
			vuna_diversion_item="SDI",
			dn_detail="DNI",
			warehouse="Vehicle",
		)
		self.target = Document(
			doctype="Delivery Note",
			name="SR",
			is_return=1,
			return_against="DN",
			company="Company",
			customer="A",
			vuna_return_request="RR",
			items=[self.row],
			docstatus=1,
			vuna_shipment_diversion="SD",
		)
		self.identities = self.enterContext(
			patch.object(service, "_identity", return_value={"B1": {"stock_qty": 48, "serials": []}})
		)

	@staticmethod
	def fail_rule(message):
		raise ValueError(message)

	def test_return_rows_preserve_transaction_uom_and_original_lot(self):
		service.validate_rows(self.target, self.doc)
		self.assertEqual(self.row.qty, -2)
		self.assertEqual(self.row.stock_qty, -48)

	def test_quantity_conversion_and_item_tampering_are_rejected(self):
		for field, value in (
			("qty", -3),
			("stock_qty", -49),
			("uom", "Pcs"),
			("item_code", "Other"),
			("conversion_factor", 1),
			("qty", float("nan")),
			("stock_qty", float("nan")),
			("conversion_factor", float("nan")),
		):
			with self.subTest(field=field, value=value):
				original = self.row[field]
				self.row[field] = value
				with self.assertRaises(ValueError):
					service.validate_rows(self.target, self.doc)
				self.row[field] = original

	def test_missing_duplicate_and_foreign_rows_are_rejected(self):
		for rows in ([], [self.row, self.row], [frappe._dict(self.row, vuna_diversion_item="Other")]):
			self.target.items = rows
			with self.assertRaises(ValueError):
				service.validate_rows(self.target, self.doc)

	def test_batch_serial_and_bundle_quantities_must_match(self):
		for groups in (
			{"Other": {"stock_qty": 48, "serials": []}},
			{"B1": {"stock_qty": 47, "serials": []}},
			{"B1": {"stock_qty": 48, "serials": ["Wrong Serial"]}},
		):
			self.identities.return_value = groups
			with self.assertRaises(ValueError):
				service.validate_rows(self.target, self.doc)

	def test_posted_return_is_required_before_onward_stock(self):
		with patch.object(service, "existing_document", return_value=None):
			with self.assertRaisesRegex(ValueError, "Submit.*Sales Return"):
				service.posted_return(self.doc)
		with patch.object(service, "existing_document", return_value=self.target):
			self.assertIs(service.posted_return(self.doc), self.target)
			self.row.warehouse = "Factory"
			with self.assertRaisesRegex(ValueError, "vehicle warehouse"):
				service.posted_return(self.doc)

	def test_customer_quality_cannot_be_assumed_from_rejection_or_approval(self):
		self.framework.get_all.return_value = []
		with self.assertRaisesRegex(ValueError, "Accepted Quality Inspection"):
			service.check_customer_quality(self.doc, self.target)
		self.framework.get_all.return_value = ["QI"]
		qi = frappe._dict(docstatus=0, status="Accepted", batch_no="B1")
		self.framework.get_doc.return_value = qi
		with self.assertRaises(ValueError):
			service.check_customer_quality(self.doc, self.target)

	def test_followup_requires_fresh_quality_evidence_after_latest_rejection(self):
		self.doc.previous_proof_of_delivery = "LAST-POD"
		self.framework.get_all.return_value = ["QI"]
		self.framework.db.get_value.return_value = "2026-11-16 10:00:00"
		qi = frappe._dict(docstatus=1, status="Accepted", batch_no="B1", creation="2026-11-16 09:00:00")
		self.framework.get_doc.return_value = qi
		with self.assertRaisesRegex(ValueError, "Inspect these goods again"):
			service.check_customer_quality(self.doc, self.target)
		qi.creation = "2026-11-16 11:00:00"
		service.check_customer_quality(self.doc, self.target)
		qi.docstatus, qi.status = 1, "Rejected"
		with self.assertRaises(ValueError):
			service.check_customer_quality(self.doc, self.target)
		qi.status = "Accepted"
		service.check_customer_quality(self.doc, self.target)
		qi.batch_no = "Other"
		with self.assertRaises(ValueError):
			service.check_customer_quality(self.doc, self.target)

	def test_retries_reopen_the_same_document_without_creating_or_submitting(self):
		with (
			patch.object(service, "document_context", return_value=self.doc),
			patch.object(service, "existing_document", return_value=self.target),
		):
			result = service.create_diversion_document("SD", "Sales Return")
		self.assertEqual(result, {"doctype": "Delivery Note", "name": "SR"})
		self.framework.new_doc.assert_not_called()
		self.doc.db_set.assert_called_once_with("sales_return", "SR", update_modified=True)

	def test_new_document_is_inserted_as_a_draft_without_submission(self):
		target = MagicMock(doctype="Delivery Note")
		target.name = "New SR"
		with (
			patch.object(service, "document_context", return_value=self.doc),
			patch.object(service, "existing_document", return_value=None),
			patch.object(service, "_reuse_sales_return", return_value=None),
			patch.object(service, "_make_sales_return", return_value=target),
		):
			service.create_diversion_document("SD", "Sales Return")
		target.insert.assert_called_once_with()
		target.submit.assert_not_called()
		self.framework.db.commit.assert_not_called()

	def test_invalid_document_or_destination_is_rejected(self):
		with self.assertRaises(ValueError):
			service.create_diversion_document("SD", "Sales Invoice")
		with patch.object(service, "document_context", return_value=self.doc):
			with self.assertRaises(ValueError):
				service.create_diversion_document("SD", "Stock Entry")
			self.doc.destination_type = "Depot"
			with self.assertRaises(ValueError):
				service.create_diversion_document("SD", "Sales Order")

	def test_cancel_return_requires_onward_documents_to_be_reversed_first(self):
		self.framework.get_doc.return_value = self.doc
		with patch.object(service, "existing_document", return_value=self.target):
			with self.assertRaisesRegex(ValueError, "onward"):
				service.prepare_document_removal(self.target)
		self.doc.db_set.assert_not_called()

	def test_cancel_clears_only_the_matching_forward_reference(self):
		self.doc.sales_return = "SR"
		self.framework.get_doc.return_value = self.doc
		with patch.object(service, "existing_document", return_value=None):
			service.prepare_document_removal(self.target)
		self.doc.db_set.assert_called_once_with("sales_return", None, update_modified=False)

	def test_plan_cannot_be_cancelled_with_active_documents(self):
		with patch.object(service, "existing_document", return_value=self.target):
			with self.assertRaises(ValueError):
				service.guard_diversion_cancellation(self.doc)

	def test_link_removal_cannot_bypass_server_validation(self):
		target = MagicMock()
		target.get_doc_before_save.return_value = frappe._dict(vuna_shipment_diversion="SD")
		target.get.return_value = None
		with self.assertRaisesRegex(ValueError, "cannot be changed"):
			service.validate_diversion_document(target)

	def test_customer_quality_cannot_be_removed_while_onward_note_exists(self):
		self.framework.db.get_value.return_value = "SD"
		self.framework.get_doc.return_value = self.doc
		with patch.object(service, "existing_document", return_value=self.target):
			with self.assertRaisesRegex(ValueError, "quality clearance"):
				service.guard_diversion_quality_removal(
					frappe._dict(reference_type="Delivery Note", reference_name="SR")
				)

	def test_sales_order_uses_new_customer_and_preserves_requested_uom(self):
		target = Document(items=[], run_method=MagicMock())
		target.update = lambda values: vars(target).update(values)
		self.framework.new_doc.return_value = target
		service._make_sales_order(self.doc)
		self.assertEqual((target.customer, target.shipping_address_name), ("B", "Address B"))
		self.assertEqual((target.items[0].qty, target.items[0].uom), (2, "Carton"))
		self.assertEqual(target.items[0].warehouse, "Vehicle")
		self.assertIsNone(target.items[0].get("rate"))
		target.run_method.assert_any_call("set_missing_values")

	def test_depot_stock_entry_is_normal_transfer_not_another_transit_loading(self):
		target = Document(doctype="Stock Entry", items=[])
		target.update = lambda values: vars(target).update(values)
		self.framework.new_doc.return_value = target
		with (
			patch.object(service, "_bundle") as bundle,
			patch(
				"vuna_transport.services.execution.allocation_transfer_service.set_allocation_stock_defaults"
			) as defaults,
		):
			service._make_stock_entry(self.doc)
		self.assertEqual(target.stock_entry_type, "Material Transfer")
		self.assertEqual(target.add_to_transit, 0)
		self.assertEqual((target.from_warehouse, target.to_warehouse), ("Vehicle", "Depot"))
		self.assertEqual((target.items[0].qty, target.items[0].transfer_qty), (2, 48))
		bundle.assert_called_once_with(target, target.items[0], self.planned)
		defaults.assert_called_once_with(target, self.doc)

	def test_return_mapper_keeps_original_price_but_receives_only_approved_quantity(self):
		self.framework.get_doc.return_value = frappe._dict(process_version="Custody and Inspection")
		self.framework.get_all.return_value = []
		row = Document(dn_detail="DNI", qty=-10, rate=1200)
		target = Document(items=[row], calculate_taxes_and_totals=MagicMock())
		with (
			patch("erpnext.controllers.sales_and_purchase_return.make_return_doc", return_value=target),
			patch.object(service, "_bundle") as bundle,
		):
			service._make_sales_return(self.doc)
		self.assertEqual((row.qty, row.stock_qty, row.rate), (-2, -48, 1200))
		self.assertEqual((row.warehouse, target.set_warehouse), ("Vehicle", "Vehicle"))
		bundle.assert_called_once_with(target, row, self.planned, inward=True)

	def test_exact_existing_vehicle_return_is_adopted_without_stock_changes(self):
		self.framework.get_doc.side_effect = [frappe._dict(name="RR"), self.target]
		self.framework.get_all.return_value = ["SR"]
		self.target.vuna_shipment_diversion = None
		self.target.db_set, self.row.db_set = MagicMock(), MagicMock()
		result = service._reuse_sales_return(self.doc)
		self.assertIs(result, self.target)
		self.target.db_set.assert_called_once_with("vuna_shipment_diversion", "SD", update_modified=True)
		self.assertEqual((self.row.warehouse, self.row.qty, self.target.docstatus), ("Vehicle", -2, 1))

	def test_vehicle_return_in_other_warehouse_is_never_rewritten(self):
		self.framework.get_doc.side_effect = [frappe._dict(name="RR"), self.target]
		self.framework.get_all.return_value = ["SR"]
		self.target.vuna_shipment_diversion = None
		self.row.warehouse = "Factory"
		with self.assertRaisesRegex(ValueError, "cannot be received again"):
			service._reuse_sales_return(self.doc)
		self.assertEqual(self.row.warehouse, "Factory")

	def test_stock_check_aggregates_rows_and_requires_actual_stock(self):
		self.doc.items.append(frappe._dict(self.planned, name="SDI-2", qty=1, stock_qty=24))
		with (
			patch("erpnext.stock.utils.get_stock_balance", return_value=71),
			patch("erpnext.stock.doctype.batch.batch.get_batch_qty", return_value=100),
		):
			with self.assertRaisesRegex(ValueError, "no longer in the vehicle"):
				service.check_vehicle_stock(self.doc)
		with (
			patch("erpnext.stock.utils.get_stock_balance", return_value=100),
			patch("erpnext.stock.doctype.batch.batch.get_batch_qty", return_value=71),
		):
			with self.assertRaisesRegex(ValueError, "batch quantity"):
				service.check_vehicle_stock(self.doc)
