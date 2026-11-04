import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.utils import flt

from vuna_transport.services.execution import depot_custody_service as service


class TestDepotCustody(unittest.TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(service, "frappe"))
		self.framework._dict = frappe._dict
		self.framework.get_precision.return_value = 6
		self.framework.get_cached_value.return_value = 0
		self.framework.db.get_value.return_value = "QUARANTINE"
		self.framework.get_installed_apps.return_value = ["nl_vms"]
		self.warehouse = frappe._dict(
			name="QUARANTINE", company="Company", disabled=0, is_group=0, warehouse_type="Stores"
		)
		self.framework.get_doc.return_value = self.warehouse

		def fail(message):
			raise ValueError(message)

		self.framework.throw.side_effect = fail
		self.enterContext(patch.object(service, "_", side_effect=lambda message: message))
		self.enterContext(
			patch.object(
				service,
				"flt",
				side_effect=lambda value, precision=None: flt(
					value, precision, rounding_method="Banker's Rounding"
				),
			)
		)
		self.row = frappe._dict(
			name="ROW",
			delivered_qty=8,
			rejected_qty=2,
			depot_received_rejected_qty=1,
			conversion_factor=10,
			uom="Carton",
			stock_uom="Pcs",
			serial_no="",
			rejected_serial_no="",
		)
		self.proof = SimpleNamespace(company="Company", items=[self.row], rejected_goods_warehouse=None)
		self.proof.get = lambda key: getattr(self.proof, key, None)

	def validate(self):
		service.validate_depot_custody(self.proof, frappe._dict(to_warehouse="TRANSIT"), "DEPOT")

	def test_split_custody_routes_separate_receipt_groups(self):
		self.validate()
		self.assertEqual(self.row.rejected_with_driver_qty, 1)
		self.assertEqual(self.proof.rejected_goods_warehouse, "QUARANTINE")
		self.assertEqual(
			[(g.kind, g.qty) for g in service.receipt_groups(self.proof)], [("Accepted", 8), ("Rejected", 1)]
		)
		groups = service.rejection_custody_groups(self.proof, "TRANSIT")
		self.assertEqual(
			{key: group.qty for key, group in groups.items()},
			{("ROW", "QUARANTINE"): 1, ("ROW", "TRANSIT"): 1},
		)

	def test_driver_only_needs_no_quarantine_configuration(self):
		self.row.depot_received_rejected_qty = 0
		self.validate()
		self.framework.db.get_value.assert_not_called()
		self.assertEqual(self.row.rejected_with_driver_qty, 2)

	def test_all_depot_choice_derives_quantity_and_serial_subset(self):
		self.row.rejected_goods_handling = service.WITH_DEPOT
		self.row.conversion_factor = 1
		self.row.rejected_serial_no = "S1\nS2"
		self.validate()
		self.assertEqual(self.row.depot_received_rejected_qty, 2)
		self.assertEqual(self.row.depot_received_rejected_serial_no, "S1\nS2")
		self.assertEqual(self.row.rejected_with_driver_qty, 0)

	def test_all_vehicle_choice_clears_previous_depot_quantity(self):
		self.row.rejected_goods_handling = service.WITH_DRIVER
		self.row.depot_received_rejected_serial_no = "S1"
		self.validate()
		self.assertEqual(self.row.depot_received_rejected_qty, 0)
		self.assertEqual(self.row.depot_received_rejected_serial_no, "")

	def test_split_choice_requires_a_real_split_on_submission(self):
		self.row.rejected_goods_handling = service.SPLIT
		for qty in (0, 2):
			self.row.depot_received_rejected_qty = qty
			with self.assertRaisesRegex(ValueError, "For Split"):
				self.validate()

	def test_no_rejection_clears_stale_split(self):
		self.row.rejected_goods_handling = service.SPLIT
		self.row.rejected_qty = 0
		self.validate()
		self.assertEqual(self.row.depot_received_rejected_qty, 0)
		self.assertIsNone(self.row.rejected_goods_handling)

	def test_unsupported_choice_is_rejected(self):
		self.row.rejected_goods_handling = "Send anywhere"
		with self.assertRaises(ValueError):
			self.validate()

	def test_invalid_quantities_are_rejected(self):
		for qty in (-1, 3, float("inf"), float("nan")):
			with self.subTest(qty=qty), self.assertRaises(ValueError):
				self.row.depot_received_rejected_qty = qty
				self.validate()

	def test_missing_configuration_blocks_depot_receipt(self):
		self.framework.db.get_value.return_value = None
		with self.assertRaisesRegex(ValueError, "Configure"):
			self.validate()

	def test_invalid_warehouses_are_rejected(self):
		for field, value in (
			("company", "Other"),
			("disabled", 1),
			("is_group", 1),
			("warehouse_type", "Transit"),
		):
			old = self.warehouse.get(field)
			self.warehouse[field] = value
			with self.subTest(field=field), self.assertRaises(ValueError):
				self.validate()
			self.warehouse[field] = old

	def test_auto_writeoff_warehouse_cannot_hold_pending_qc_stock(self):
		self.warehouse.is_rejected_warehouse = 1
		with self.assertRaisesRegex(ValueError, "automatically writes off"):
			self.validate()

	def test_serial_split_preserves_exact_rejected_identities(self):
		self.row.conversion_factor = 1
		self.row.rejected_serial_no = "S1\nS2"
		self.row.depot_received_rejected_serial_no = "S1"
		self.validate()
		groups = service.rejection_custody_groups(self.proof, "TRANSIT")
		self.assertEqual(groups[("ROW", "TRANSIT")].serial_no, "S2")
		self.row.depot_received_rejected_serial_no = "S3"
		with self.assertRaises(ValueError):
			self.validate()

	def test_fractional_whole_uom_receipt_is_rejected(self):
		self.framework.get_cached_value.return_value = 1
		self.row.depot_received_rejected_qty = 0.5
		with self.assertRaises(ValueError):
			self.validate()
