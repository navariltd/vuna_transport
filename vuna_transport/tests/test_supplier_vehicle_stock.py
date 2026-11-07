"""Supplier receipt/vehicle stock transfer authority and quantity boundaries."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import frappe

from vuna_transport.services.execution import supplier_vehicle_receipt_service as receipts
from vuna_transport.services.execution import supplier_vehicle_transfer_service as transfers


def document(**values):
	doc = SimpleNamespace(**values)
	doc.get = lambda key: getattr(doc, key, None)
	doc.get_doc_before_save = lambda: None
	return doc


def item(**values):
	row = frappe._dict(
		name="PRI",
		purchase_order_item="POI",
		purchase_order="PO",
		item_code="PACK",
		qty=2,
		stock_qty=20,
		uom="Carton",
		stock_uom="Pcs",
		conversion_factor=10,
		rejected_qty=0,
		warehouse="VEHICLE",
		batch_no=None,
		serial_no="",
		serial_and_batch_bundle=None,
	)
	row.update(values)
	row.precision = lambda field: 6
	return row


class TestSupplierVehicleReceipt(TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(receipts, "frappe"))
		self.framework.throw.side_effect = ValueError
		self.enterContext(patch.object(receipts, "_", side_effect=lambda text: text))
		self.enterContext(patch.object(receipts, "require_document_permission"))
		self.enterContext(patch.object(receipts, "reserved_purchase_quantities", return_value={}))
		self.source = {
			"source_item": "POI",
			"item_code": "PACK",
			"transaction_qty": 15,
			"transaction_uom": "Carton",
			"stock_uom": "Pcs",
			"conversion_factor": 10,
		}
		self.context = self.enterContext(
			patch.object(
				receipts,
				"receipt_context",
				return_value=(
					frappe._dict(name="PO", company="Company", supplier="Supplier"),
					frappe._dict(name="VA"),
					"VEHICLE",
					[{"items": [self.source]}],
				),
			)
		)
		self.doc = document(
			name="PR",
			company="Company",
			supplier="Supplier",
			is_return=0,
			set_warehouse="VEHICLE",
			vuna_pickup_purchase_order="PO",
			vuna_pickup_allocation="VA",
			vuna_pickup_transit_warehouse="VEHICLE",
			items=[item()],
		)

	def test_valid_partial_collection_uses_original_requested_uom(self):
		receipts.validate_vehicle_purchase_receipt(self.doc)
		self.assertEqual(self.doc.items[0].uom, "Carton")

	def test_wrong_supplier_company_or_vehicle_destination_is_rejected(self):
		for field, value in (
			("supplier", "Other"),
			("company", "Other"),
			("is_return", 1),
			("vuna_pickup_transit_warehouse", "MAIN"),
		):
			with patch.object(self.doc, field, value), self.assertRaises(ValueError):
				receipts.validate_vehicle_purchase_receipt(self.doc)

	def test_rows_cannot_forge_po_identity_quantities_uom_or_receiving_warehouse(self):
		for changes in (
			{"qty": 16},
			{"qty": float("nan")},
			{"qty": -1},
			{"warehouse": "MAIN"},
			{"uom": "Pcs"},
			{"conversion_factor": 1},
			{"purchase_order_item": "OTHER"},
			{"item_code": "OTHER"},
			{"rejected_qty": 1},
		):
			with patch.dict(self.doc.items[0], changes), self.assertRaises(ValueError):
				receipts.validate_vehicle_purchase_receipt(self.doc)

	def test_other_drafts_reserve_unreceived_po_quantity(self):
		self.doc.items[0].qty = 13
		with (
			patch.object(receipts, "reserved_purchase_quantities", return_value={"POI": 3}),
			self.assertRaises(ValueError),
		):
			receipts.validate_vehicle_purchase_receipt(self.doc)

	def test_saved_receipt_cannot_discard_its_allocation_link(self):
		previous = frappe._dict(
			vuna_pickup_purchase_order="PO",
			vuna_pickup_allocation="VA",
			vuna_pickup_transit_warehouse="VEHICLE",
		)
		self.doc.get_doc_before_save = lambda: previous
		self.doc.vuna_pickup_allocation = None
		with self.assertRaises(ValueError):
			receipts.validate_vehicle_purchase_receipt(self.doc)

	def test_receipt_cancellation_requires_removing_downstream_transfers(self):
		self.framework.db.exists.return_value = True
		with self.assertRaises(ValueError):
			receipts.guard_vehicle_receipt_cancellation(self.doc)

	def test_retry_returns_existing_draft_instead_of_remapping_stock(self):
		self.framework.db.get_value.return_value = "EXISTING-PR"
		with patch.object(receipts, "require_doctype_permission"):
			self.assertEqual(receipts.create_vehicle_purchase_receipt("PO", "VA"), "EXISTING-PR")
		self.framework.new_doc.assert_not_called()

	def test_driver_without_purchasing_authority_cannot_create_inventory(self):
		with (
			patch.object(receipts, "require_doctype_permission", side_effect=PermissionError),
			self.assertRaises(PermissionError),
		):
			receipts.create_vehicle_purchase_receipt("PO", "VA")
		self.context.assert_not_called()


class TestSupplierVehicleTransfer(TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(transfers, "frappe"))
		self.framework._dict.side_effect = frappe._dict
		self.framework.throw.side_effect = ValueError
		self.enterContext(patch.object(transfers, "_", side_effect=lambda text: text))
		self.enterContext(patch.object(transfers, "require_document_permission"))
		self.receipt = document(
			name="PR", company="Company", vuna_pickup_transit_warehouse="VEHICLE", items=[item()]
		)
		self.context = self.enterContext(
			patch.object(transfers, "transfer_context", return_value=self.receipt)
		)
		self.enterContext(patch.object(transfers, "receiving_warehouse"))
		self.group = frappe._dict(source=self.receipt.items[0], qty=20, serials=set())
		self.enterContext(patch.object(transfers, "receipt_groups", return_value={("PRI", ""): self.group}))
		self.row = item(
			vuna_supplier_receipt_item="PRI", s_warehouse="VEHICLE", t_warehouse="MAIN", transfer_qty=20
		)
		self.doc = document(
			name="SE",
			company="Company",
			purpose="Material Transfer",
			add_to_transit=0,
			outgoing_stock_entry=None,
			from_warehouse="VEHICLE",
			vuna_supplier_purchase_receipt="PR",
			items=[self.row],
		)

	def test_supplier_arrival_is_a_normal_transfer(self):
		transfers.validate_vehicle_material_transfer(self.doc)
		self.assertIsNone(self.doc.outgoing_stock_entry)

	def test_add_to_transit_end_transit_or_material_receipt_is_forbidden(self):
		for field, value in (
			("add_to_transit", 1),
			("outgoing_stock_entry", "OUT"),
			("purpose", "Material Receipt"),
			("company", "OTHER"),
		):
			with patch.object(self.doc, field, value), self.assertRaises(ValueError):
				transfers.validate_vehicle_material_transfer(self.doc)

	def test_transfer_retains_receipt_item_uom_and_available_quantity(self):
		for changes in (
			{"vuna_supplier_receipt_item": "OTHER"},
			{"transfer_qty": 21},
			{"uom": "Pcs"},
			{"s_warehouse": "OTHER"},
			{"transfer_qty": float("inf")},
		):
			with patch.dict(self.row, changes), self.assertRaises(ValueError):
				transfers.validate_vehicle_material_transfer(self.doc)

	def test_partial_transfer_is_allowed_and_reservations_prevent_reuse(self):
		self.row.transfer_qty, self.row.qty = 10, 1
		transfers.validate_vehicle_material_transfer(self.doc)
		self.group.qty = 5
		with self.assertRaises(ValueError):
			transfers.validate_vehicle_material_transfer(self.doc)

	def test_unreceived_serial_numbers_cannot_be_substituted(self):
		self.group.serials = {"S1", "S2"}
		self.group.qty = self.row.transfer_qty = 2
		self.row.conversion_factor = self.receipt.items[0].conversion_factor = 1
		self.row.serial_no = "S1\nS3"
		with self.assertRaises(ValueError):
			transfers.validate_vehicle_material_transfer(self.doc)
		self.row.serial_no = "S1\nS2"
		transfers.validate_vehicle_material_transfer(self.doc)


class TestSupplierTransferContext(TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(transfers, "frappe"))
		self.framework.throw.side_effect = ValueError
		self.framework.db.exists.return_value = False
		self.framework.db.sql.return_value = []
		self.enterContext(patch.object(transfers, "_", side_effect=lambda text: text))
		self.enterContext(patch.object(transfers, "require_document_permission"))
		self.receipt = document(
			name="PR",
			docstatus=1,
			is_return=0,
			vuna_pickup_purchase_order="PO",
			vuna_pickup_transit_warehouse="VEHICLE",
		)
		self.receipt.reload = lambda: None
		self.framework.get_doc.return_value = self.receipt

	def test_only_submitted_purchase_stock_is_transferable(self):
		self.assertIs(transfers.transfer_context("PR"), self.receipt)
		self.framework.db.get_value.assert_called_with("Purchase Receipt", "PR", "name", for_update=True)
		self.receipt.docstatus = 0
		with self.assertRaises(ValueError):
			transfers.transfer_context("PR")

	def test_supplier_returns_block_reuse_of_original_received_quantity(self):
		self.framework.db.exists.return_value = True
		with self.assertRaises(ValueError):
			transfers.transfer_context("PR")
		self.framework.db.exists.return_value = False
		self.framework.db.sql.return_value = [("RETURN-INVOICE",)]
		with self.assertRaises(ValueError):
			transfers.transfer_context("PR")

	def test_target_must_be_active_same_company_storage(self):
		warehouse = frappe._dict(company="Company", disabled=0, is_group=0, warehouse_type="Stores")
		self.framework.get_doc.return_value = warehouse
		transfers.receiving_warehouse("MAIN", "Company")
		for changes in (
			{"company": "OTHER"},
			{"disabled": 1},
			{"is_group": 1},
			{"warehouse_type": "Transit"},
		):
			with patch.dict(warehouse, changes), self.assertRaises(ValueError):
				transfers.receiving_warehouse("MAIN", "Company")
		warehouse.is_rejected_warehouse = 1
		self.framework.get_installed_apps.return_value = ["nl_vms"]
		with self.assertRaises(ValueError):
			transfers.receiving_warehouse("MAIN", "Company")
