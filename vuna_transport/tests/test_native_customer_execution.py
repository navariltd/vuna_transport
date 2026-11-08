"""Native customer execution keeps exact source rows and requested UOMs."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from vuna_transport.services.execution import customer_reference_service as service


class TestNativeCustomerExecution(unittest.TestCase):
	def setUp(self):
		self.row = frappe._dict(
			name="LI-1",
			parent="TL-1",
			transport_demand="TD-1",
			transport_demand_item="DI-1",
			uom="Carton",
			stock_uom="Pcs",
			conversion_factor=10,
			allocated_quantity=2,
			delivery_address="Address",
		)
		self.load = frappe._dict(
			name="TL-1",
			docstatus=1,
			company="Company",
			status="Allocated",
			active_vehicle_allocation="VA-1",
			delivery_orders=[self.row],
		)
		self.source = frappe._dict(
			name="TD-1",
			docstatus=1,
			company="Company",
			customer="Customer",
			movement_type="Customer Delivery",
			source_document_type="Sales Order",
			sales_order="SO-1",
		)
		self.item = frappe._dict(parent="TD-1", sales_order_item="SI-1", item_code="MILK")
		self.note_item = frappe._dict(
			vuna_delivery_load_order="LI-1",
			so_detail="SI-1",
			against_sales_order="SO-1",
			item_code="MILK",
			uom="Carton",
			stock_uom="Pcs",
			conversion_factor=10,
			qty=2,
			warehouse="Warehouse",
		)
		self.note = SimpleNamespace(
			name="DN-1",
			company="Company",
			customer="Customer",
			shipping_address_name="Address",
			vuna_transport_demand=None,
			vuna_delivery_order=None,
		)
		self.note.items = [self.note_item]
		self.note.get = lambda key, default=None: getattr(self.note, key, default)

	def validate(self, prior=0):
		with (
			patch.object(service, "frappe") as framework,
			patch.object(service, "get_item", return_value=self.item),
			patch(
				"vuna_transport.services.execution.dispatch_guard_service.get_locked_delivery_load",
				return_value=self.load,
			),
			patch("vuna_transport.services.execution.dispatch_guard_service.validate_load_is_operational"),
		):
			framework.get_all.return_value = [self.row]
			framework.get_doc.return_value = self.source
			framework.db.get_value.return_value = "Company"
			framework.db.sql.return_value = [("LI-1", prior)] if prior else []
			framework.throw.side_effect = ValueError
			service.validate_customer_note(self.note, self.load)

	def test_exact_source_row_and_transaction_uom_are_preserved(self):
		self.validate()
		self.assertEqual(self.note.vuna_transport_demand, "TD-1")
		self.assertFalse(self.note.vuna_delivery_order)

	def test_spoofed_sales_order_child_is_rejected(self):
		self.note_item.so_detail = "other-row"
		with self.assertRaises(ValueError):
			self.validate()

	def test_changed_uom_or_factor_is_rejected(self):
		for key, value in (("uom", "Pcs"), ("conversion_factor", 1)):
			with self.subTest(key=key):
				original = self.note_item[key]
				self.note_item[key] = value
				with self.assertRaises(ValueError):
					self.validate()
				self.note_item[key] = original

	def test_draft_and_submitted_notes_cannot_exceed_exact_allocation(self):
		with self.assertRaises(ValueError):
			self.validate(prior=1)

	def test_nonfinite_or_negative_quantity_is_rejected(self):
		for quantity in (float("nan"), float("inf"), -1, 0):
			with self.subTest(quantity=quantity), self.assertRaises(ValueError):
				self.note_item.qty = quantity
				self.validate()

	def test_foreign_customer_and_warehouse_are_rejected(self):
		self.source.customer = "Other"
		with self.assertRaises(ValueError):
			self.validate()

	def test_legacy_shadow_link_is_not_native(self):
		self.note.vuna_delivery_order = "DO-1"
		self.note.vuna_transport_demand = "TD-1"
		with patch.object(service, "frappe") as framework:
			framework.db.exists.return_value = False
			self.assertFalse(service.native_note(self.note))
		self.assertEqual(
			service.order_type(frappe._dict(delivery_order="DO-1", transport_demand="TD-1")), "Delivery Order"
		)

	def test_submission_readiness_requires_exact_quantity_not_just_a_note(self):
		with (
			patch.object(service, "frappe") as framework,
			patch(
				"vuna_transport.services.execution.lineage_service.get_delivery_notes_for_loads",
				return_value=["DN-1"],
			),
		):
			framework.get_all.return_value = [frappe._dict(name="DN-1", docstatus=1)]
			for quantity, ready in ((1, False), (2, True), (3, False)):
				framework.db.sql.return_value = [("LI-1", quantity)]
				self.assertEqual(service.note_submission_status(self.load)["ready"], ready)
			framework.db.sql.return_value = [("LI-1", 2)]
			framework.get_all.return_value.append(frappe._dict(name="DN-2", docstatus=0))
			self.assertFalse(service.note_submission_status(self.load)["ready"])

	def test_completion_compares_each_item_not_sum_of_unlike_uoms(self):
		demand = SimpleNamespace(
			name="TD-1", docstatus=1, source_document_type="Sales Order", source_document="SO-1"
		)
		demand.items = [
			frappe._dict(name="DI-1", stock_qty=10, allocated_stock_qty=10, reserved_stock_qty=0),
			frappe._dict(name="DI-2", stock_qty=1, allocated_stock_qty=1, reserved_stock_qty=0),
		]
		with patch.object(service, "frappe") as framework:
			framework.get_doc.return_value = demand
			framework.db.sql.return_value = [
				frappe._dict(transport_demand_item="DI-1", covered=11, accepted=11)
			]
			service.recompute_native_demand("TD-1")
			self.assertEqual(framework.db.set_value.call_args.args[3], "Partially Delivered")
			framework.db.sql.return_value = [
				frappe._dict(transport_demand_item=row.name, covered=row.stock_qty, accepted=row.stock_qty)
				for row in demand.items
			]
			service.recompute_native_demand("TD-1")
			self.assertEqual(framework.db.set_value.call_args.args[3], "Delivered")
