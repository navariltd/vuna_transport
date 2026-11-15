"""Quantity, identity and inspection boundaries for manual depot returns."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.utils import flt

from vuna_transport.services.execution import depot_return_inspection_service as inspection
from vuna_transport.services.execution import manual_depot_return_service as manual


class Row(SimpleNamespace):
	def get(self, key, default=None):
		return getattr(self, key, default)

	def set(self, key, value):
		setattr(self, key, value)


class TestDepotInspection(unittest.TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(inspection, "frappe"))
		self.enterContext(
			patch(
				"vuna_transport.services.execution.return_inspection_quality_service.frappe", self.framework
			)
		)
		self.framework.throw.side_effect = ValueError
		self.framework.db.exists.return_value = False
		self.framework.db.get_value.return_value = None
		self.framework.db.get_single_value.return_value = 0
		self.framework.get_installed_apps.return_value = ["nl_vms"]
		self.framework.get_precision.return_value = 3
		self.enterContext(patch.object(inspection, "_", side_effect=lambda text: text))
		self.enterContext(patch.object(inspection, "require_document_permission"))
		self.enterContext(patch.object(inspection, "validate_uom_is_integer"))
		self.enterContext(patch("vuna_transport.services.execution.return_v2_service._validate_warehouse"))
		self.enterContext(
			patch.object(
				inspection,
				"flt",
				side_effect=lambda value, precision=None: flt(
					value, precision, rounding_method="Banker's Rounding"
				),
			)
		)
		self.source = frappe._dict(
			name="SED-1",
			return_request="RR-1",
			return_request_item="RRI-1",
			item_code="MILK",
			stock_uom="Pcs",
			stock_qty=20,
			batch_no="B1",
			serial_no="",
			serial_and_batch_bundle="BUNDLE-1",
			warehouse="Holding",
		)
		self.sources = self.enterContext(
			patch.object(inspection, "receipt_sources", return_value=[self.source])
		)
		self.framework.get_doc.return_value = Row(name="STE-1", company="Company")
		self.doc = Row(
			name="RIN-1",
			receiving_stock_entry="STE-1",
			sales_return=None,
			return_request="RR-1",
			docstatus=1,
			results=[self.result(10), self.result(10, "Rejected")],
		)

	def result(self, qty, result="Accepted"):
		return Row(
			idx=1,
			stock_entry_detail="SED-1",
			item_code="MILK",
			delivery_note_item=None,
			batch_no="B1",
			inspection_qty=qty,
			result=result,
			reason="Damaged" if result == "Rejected" else None,
			target_warehouse="Saleable" if result == "Accepted" else "Rejected Holding",
			serial_no="",
			quality_inspection=None,
		)

	def test_split_outcomes_reconcile_stock_uom(self):
		inspection.validate_inspection(self.doc)
		self.assertEqual(
			(self.doc.total_received_stock_qty, self.doc.accepted_stock_qty, self.doc.rejected_stock_qty),
			(20, 10, 10),
		)
		self.assertEqual(self.doc.results[0].stock_uom, "Pcs")

	def test_partial_draft_is_allowed(self):
		self.doc.docstatus, self.doc.results = 0, [self.result(12)]
		inspection.validate_inspection(self.doc)
		self.assertEqual(self.doc.pending_stock_qty, 8)

	def test_partial_inspection_cannot_submit(self):
		self.doc.results = [self.result(12)]
		with self.assertRaises(ValueError):
			inspection.validate_inspection(self.doc)

	def test_over_inspection_rejected_even_in_draft(self):
		self.doc.docstatus, self.doc.results = 0, [self.result(21)]
		with self.assertRaises(ValueError):
			inspection.validate_inspection(self.doc)

	def test_wrong_item_rejected(self):
		self.doc.results[0].item_code = "OTHER"
		with self.assertRaises(ValueError):
			inspection.validate_inspection(self.doc)

	def test_wrong_batch_rejected(self):
		self.doc.results[0].batch_no = "OTHER"
		with self.assertRaises(ValueError):
			inspection.validate_inspection(self.doc)

	def test_rejected_group_needs_reason(self):
		self.doc.results[1].reason = None
		with self.assertRaises(ValueError):
			inspection.validate_inspection(self.doc)

	def test_target_required_only_at_submission(self):
		self.doc.results[0].target_warehouse = None
		self.doc.docstatus = 0
		inspection.validate_inspection(self.doc)
		self.doc.docstatus = 1
		with self.assertRaises(ValueError):
			inspection.validate_inspection(self.doc)

	def test_no_duplicate_active_inspection(self):
		self.framework.db.exists.return_value = True
		with self.assertRaises(ValueError):
			inspection.validate_inspection(self.doc)

	def test_nonfinite_quantity_rejected(self):
		self.doc.results[0].inspection_qty = float("inf")
		with self.assertRaises(ValueError):
			inspection.validate_inspection(self.doc)

	def test_serials_cannot_overlap_groups(self):
		self.source.update(stock_qty=2, serial_no="S1\nS2")
		self.doc.results = [self.result(1), self.result(1)]
		for row in self.doc.results:
			row.serial_no = "S1"
		with self.assertRaises(ValueError):
			inspection.validate_inspection(self.doc)

	def test_unique_serial_groups_allowed(self):
		self.source.update(stock_qty=2, serial_no="S1\nS2")
		self.doc.results = [self.result(1), self.result(1)]
		self.doc.results[0].serial_no, self.doc.results[1].serial_no = "S1", "S2"
		inspection.validate_inspection(self.doc)

	def test_setting_requires_qi_for_every_group_on_submit(self):
		self.framework.db.get_single_value.return_value = 1
		with self.assertRaises(ValueError):
			inspection.validate_inspection(self.doc)

	def test_setting_does_not_block_draft_preparation(self):
		self.framework.db.get_single_value.return_value = 1
		self.doc.docstatus = 0
		inspection.validate_inspection(self.doc)

	def test_same_warehouse_is_not_a_stock_transfer(self):
		self.doc.results[0].target_warehouse = "Holding"
		with self.assertRaises(ValueError):
			inspection.validate_inspection(self.doc)

	def test_warehouse_with_automatic_writeoff_is_blocked(self):
		self.framework.db.get_value.side_effect = (
			lambda dt, name, field, **kw: 1 if field == "is_rejected_warehouse" else None
		)
		with self.assertRaises(ValueError):
			inspection.validate_inspection(self.doc)


class TestManualDepotSource(unittest.TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(manual, "frappe"))
		self.framework._dict.side_effect = frappe._dict
		self.framework.throw.side_effect = ValueError
		self.enterContext(patch.object(manual, "_", side_effect=lambda text: text))
		self.enterContext(
			patch.object(
				manual, "flt", side_effect=lambda value: flt(value, rounding_method="Banker's Rounding")
			)
		)
		self.identity = self.enterContext(
			patch.object(
				manual,
				"stock_identities",
				return_value={"B1": {"stock_qty": 20, "serials": []}, "B2": {"stock_qty": 30, "serials": []}},
			)
		)
		self.stock = Row(
			docstatus=1,
			add_to_transit=0,
			items=[
				Row(
					name="SED-1",
					item_code="MILK",
					item_name="Milk",
					t_warehouse="Depot",
					uom="Carton",
					stock_uom="Pcs",
					transfer_qty=50,
					conversion_factor=10,
					serial_and_batch_bundle="BUNDLE",
				)
			],
		)

	def test_original_requested_uom_and_batch_groups_preserved(self):
		rows = manual.source_options(self.stock, "Depot")
		self.assertEqual([row.delivered_qty for row in rows], [2, 3])
		self.assertTrue(all(row.uom == "Carton" and row.source_bundle == "BUNDLE" for row in rows))

	def test_add_to_transit_is_not_a_depot_receipt(self):
		self.stock.add_to_transit = 1
		with self.assertRaises(ValueError):
			manual.source_options(self.stock, "Depot")

	def test_wrong_warehouse_not_selectable(self):
		with self.assertRaises(ValueError):
			manual.source_options(self.stock, "Other")

	def test_draft_receipt_not_selectable(self):
		self.stock.docstatus = 0
		with self.assertRaises(ValueError):
			manual.source_options(self.stock, "Depot")


class TestManualDepotApproval(unittest.TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(manual, "frappe"))
		self.framework.throw.side_effect = ValueError
		self.framework.db.get_value.return_value = None
		self.framework.db.sql.return_value = []
		self.framework.get_precision.return_value = 3
		self.enterContext(patch.object(manual, "_", side_effect=lambda value: value))
		self.enterContext(
			patch.object(
				manual,
				"flt",
				side_effect=lambda value, precision=None: flt(
					value, precision, rounding_method="Banker's Rounding"
				),
			)
		)
		self.enterContext(patch.object(manual, "require_document_permission"))
		self.enterContext(patch.object(manual, "validate_uom_is_integer"))
		self.enterContext(patch("vuna_transport.services.execution.return_v2_service._validate_warehouse"))
		self.enterContext(patch("vuna_transport.services.execution.return_service._apply_return_state"))
		self.enterContext(patch("vuna_transport.services.execution.depot_return_service.depot_return_state"))
		self.framework.get_doc.return_value = Row(name="STE-1", company="Company")
		self.source = frappe._dict(
			stock_entry_detail="SED-1",
			batch_no="B1",
			item_code="MILK",
			item_name="Milk",
			delivered_qty=2,
			uom="Carton",
			stock_uom="Pcs",
			conversion_factor=10,
			serial_no="",
			source_bundle="BUNDLE",
			custody_warehouse="Depot",
		)
		self.enterContext(patch.object(manual, "source_options", return_value=[self.source]))
		self.row = Row(
			stock_entry_detail="SED-1", item_code="MILK", batch_no="B1", serial_no="", requested_return_qty=1
		)
		self.request = Row(
			name="RR-1",
			source_stock_entry="STE-1",
			company="Company",
			source_warehouse="Depot",
			return_warehouse="Holding",
			initiation_date="2026-11-05",
			items=[self.row],
		)

	def test_approval_locks_original_receipt_and_preserves_uom(self):
		manual.validate_manual_depot_return(self.request)
		self.framework.db.get_value.assert_any_call("Stock Entry", "STE-1", "name", for_update=True)
		self.assertEqual((self.row.uom, self.row.conversion_factor), ("Carton", 10))

	def test_other_requests_reserve_original_receipt_quantity(self):
		self.framework.db.sql.return_value = [
			frappe._dict(stock_entry_detail="SED-1", batch_no="B1", requested_return_qty=2, serial_no="")
		]
		with self.assertRaises(ValueError):
			manual.validate_manual_depot_return(self.request)

	def test_over_return_is_rejected(self):
		self.row.requested_return_qty = 3
		with self.assertRaises(ValueError):
			manual.validate_manual_depot_return(self.request)

	def test_customer_documents_cannot_be_mixed_into_depot_returns(self):
		self.request.customer = "Customer"
		with self.assertRaises(ValueError):
			manual.validate_manual_depot_return(self.request)

	def test_source_company_is_enforced(self):
		self.request.company = "Other"
		with self.assertRaises(ValueError):
			manual.validate_manual_depot_return(self.request)

	def test_same_warehouse_is_rejected(self):
		self.request.return_warehouse = self.request.source_warehouse
		with self.assertRaises(ValueError):
			manual.validate_manual_depot_return(self.request)
