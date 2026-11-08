import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.utils import flt

from vuna_transport.services.execution import collection_reference_service as service


def record(**values):
	row = SimpleNamespace(**values)
	row.get = lambda key, default=None: getattr(row, key, default)
	return row


class TestCollectionReference(unittest.TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(service, "frappe"))
		self.framework.throw.side_effect = lambda message, *args: (_ for _ in ()).throw(ValueError(message))
		self.framework.get_precision.return_value = 6
		self.framework.db.sql.return_value = []
		self.framework.db.get_value.side_effect = (
			lambda doctype, name, field, **kwargs: (1, 0)
			if isinstance(field, list)
			else 1
			if doctype == "Item"
			else "Stores"
		)
		self.enterContext(patch.object(service, "_", side_effect=lambda text: text))
		self.enterContext(
			patch.object(
				service,
				"flt",
				side_effect=lambda value, precision=None: flt(
					value, precision, rounding_method="Banker's Rounding"
				),
			)
		)
		self.original_context = service.stop_context
		self.context = self.enterContext(
			patch.object(
				service,
				"stop_context",
				return_value={
					"customer": "CUSTOMER-1",
					"warehouses": ["DEPOT"],
					"reference_types": ["Delivery Note", "Stock Entry"],
					"location": "Stop",
				},
			)
		)
		self.trip = record(name="TRIP-1", company="Company")
		self.row = record(
			name="ROW-1",
			item_code="MILK",
			item_name="Milk",
			qty=10,
			uom="Carton",
			stock_uom="Pcs",
			conversion_factor=10,
			stock_qty=100,
			transfer_qty=100,
			batch_no="BATCH-1",
			serial_no="",
			serial_and_batch_bundle=None,
			t_warehouse="DEPOT",
		)
		self.source = record(
			name="DN-1",
			docstatus=1,
			company="Company",
			customer="CUSTOMER-1",
			is_return=0,
			add_to_transit=0,
			items=[self.row],
		)
		self.framework.get_doc.return_value = self.source

	def test_customer_reference_is_limited_to_its_document_rows(self):
		result = service.reference_options(self.trip, "STOP-1", "Delivery Note", "DN-1")
		self.assertEqual(result["party_type"], "Customer")
		self.assertEqual(
			[(row["name"], row["reference_row"], row["uom"], row["qty"]) for row in result["items"]],
			[("MILK", "ROW-1", "Carton", 10)],
		)

	def test_other_customer_document_is_rejected(self):
		self.source.customer = "OTHER-CUSTOMER"
		with self.assertRaisesRegex(ValueError, "stop's customer"):
			service.reference_options(self.trip, "STOP-1", "Delivery Note", "DN-1")

	def test_draft_cancelled_return_and_cross_company_documents_are_rejected(self):
		for field, value in (("docstatus", 0), ("docstatus", 2), ("is_return", 1), ("company", "Other")):
			with self.subTest(field=field, value=value):
				original = getattr(self.source, field)
				setattr(self.source, field, value)
				with self.assertRaisesRegex(ValueError, "submitted, non-return"):
					service.reference_options(self.trip, "STOP-1", "Delivery Note", "DN-1")
				setattr(self.source, field, original)

	def test_unsupported_document_types_are_rejected_before_document_read(self):
		with self.assertRaisesRegex(ValueError, "supported"):
			service.reference_options(self.trip, "STOP-1", "Purchase Invoice", "INVOICE-1")
		self.framework.get_doc.assert_not_called()

	def test_missing_reference_does_not_expose_global_items(self):
		with self.assertRaisesRegex(ValueError, "Provide"):
			service.reference_options(self.trip, "STOP-1", "Delivery Note", "")
		self.framework.get_doc.assert_not_called()

	def test_depot_source_must_have_received_goods_into_stop_warehouse(self):
		self.row.t_warehouse = "OTHER-DEPOT"
		with self.assertRaisesRegex(ValueError, "no received stock rows"):
			service.reference_options(self.trip, "STOP-1", "Stock Entry", "SE-1")

	def test_loading_in_transit_is_not_a_depot_receipt(self):
		self.source.add_to_transit = 1
		with self.assertRaisesRegex(ValueError, "no received stock rows"):
			service.reference_options(self.trip, "STOP-1", "Stock Entry", "SE-1")

	def test_warehouse_is_derived_from_the_selected_stock_row(self):
		result = service.reference_options(self.trip, "STOP-1", "Stock Entry", "SE-1")
		self.assertEqual(result["party_type"], "Depot")
		self.assertEqual(result["items"][0]["warehouse"], "DEPOT")

	def test_bundle_identity_takes_precedence_and_expands_multiple_batches(self):
		self.row.serial_and_batch_bundle = "BUNDLE-1"
		bundle = record(
			entries=[
				record(batch_no="ACTUAL-1", qty=-60, serial_no=None),
				record(batch_no="ACTUAL-2", qty=-40, serial_no=None),
			]
		)
		self.framework.get_doc.side_effect = (
			lambda doctype, name: bundle if doctype == "Serial and Batch Bundle" else self.source
		)
		items = service.reference_options(self.trip, "STOP-1", "Delivery Note", "DN-1")["items"]
		self.assertEqual([(row["batch_no"], row["qty"]) for row in items], [("ACTUAL-1", 6), ("ACTUAL-2", 4)])

	def _proof(self):
		return record(
			name="POD-1",
			collection_reference_doctype="Delivery Note",
			collection_reference_name="DN-1",
			items=[
				record(
					collection_reference_row="ROW-1",
					item_code="MILK",
					uom="Carton",
					batch_no="BATCH-1",
					stock_qty=20,
					serial_no="",
				)
			],
		)

	def test_forged_item_row_is_rejected(self):
		proof = self._proof()
		proof.items[0].collection_reference_row = "NOT-IN-DOCUMENT"
		with self.assertRaisesRegex(ValueError, "referenced document"):
			service.validate_reference_rows(
				proof, service.reference_options(self.trip, "STOP-1", "Delivery Note", "DN-1")
			)

	def test_multiple_reports_cannot_exceed_source_batch_quantity(self):
		self.framework.db.sql.return_value = [
			record(collection_reference_row="ROW-1", batch_no="BATCH-1", stock_qty=90, serial_no="")
		]
		with self.assertRaisesRegex(ValueError, "exceed"):
			service.validate_reference_rows(
				self._proof(), service.reference_options(self.trip, "STOP-1", "Delivery Note", "DN-1")
			)

	def test_serials_must_belong_to_the_referenced_stock_row(self):
		proof = self._proof()
		proof.items[0].serial_no = "OTHER-SERIAL"
		with self.assertRaisesRegex(ValueError, "must belong"):
			service.validate_reference_rows(
				proof, service.reference_options(self.trip, "STOP-1", "Delivery Note", "DN-1")
			)

	def test_customer_stop_derives_customer_without_user_selection(self):
		self.framework.db.get_value.return_value = record(name="STOP-1", customer="CUSTOMER-1", address=None)
		self.framework.db.get_value.side_effect = None
		self.framework.get_all.return_value = []
		context = self.original_context(self.trip, "STOP-1")
		self.assertEqual((context["customer"], context["reference_types"]), ("CUSTOMER-1", ["Delivery Note"]))

	def test_depot_and_mixed_stop_derive_task_destination_warehouse(self):
		for customer in (None, "CUSTOMER-1"):
			with self.subTest(customer=customer):
				self.framework.db.get_value.side_effect = (
					lambda doctype, *args, **kwargs: record(
						name="STOP-1", customer=customer, address="ADDRESS"
					)
					if doctype == "Delivery Stop"
					else "DEPOT"
				)
				self.framework.get_all.return_value = [
					record(task_type="Transfer Receipt", transport_demand="DEMAND-1", transport_load="LOAD-1")
				]
				context = self.original_context(self.trip, "STOP-1")
				self.assertEqual(context["warehouses"], ["DEPOT"])
				self.assertEqual(
					context["reference_types"], (["Delivery Note"] if customer else []) + ["Stock Entry"]
				)

	def test_unidentified_stop_cannot_fall_back_to_manual_party_selection(self):
		self.framework.db.get_value.side_effect = None
		self.framework.db.get_value.return_value = record(name="STOP-1", customer=None, address=None)
		self.framework.get_all.return_value = []
		with self.assertRaisesRegex(ValueError, "customer or an explicitly linked"):
			self.original_context(self.trip, "STOP-1")

	def test_repeated_serial_report_is_rejected_even_with_quantity_remaining(self):
		proof = self._proof()
		proof.items[0].serial_no = self.row.serial_no = "SERIAL-1"
		self.framework.db.sql.return_value = [
			record(collection_reference_row="ROW-1", batch_no="BATCH-1", stock_qty=1, serial_no="SERIAL-1")
		]
		with self.assertRaisesRegex(ValueError, "already been reported"):
			service.validate_reference_rows(
				proof, service.reference_options(self.trip, "STOP-1", "Delivery Note", "DN-1")
			)
