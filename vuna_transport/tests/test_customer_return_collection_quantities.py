import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.utils import flt

from vuna_transport.services.execution import return_service as service


class TestCustomerReturnCollectionQuantities(unittest.TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(service, "frappe"))
		self.framework.get_precision.return_value = 6

		def throw(message):
			raise ValueError(message)

		self.framework.throw.side_effect = throw
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
		self.item = frappe._dict(
			name="RRI-1",
			item_code="MILK",
			delivery_note_item="DNI-1",
			sales_invoice_item="SII-1",
			requested_return_qty=2,
			conversion_factor=10,
			received_qty=0,
		)
		self.request = Mock()
		self.request.name = "RR-1"
		self.request.get.side_effect = lambda key: "Customer" if key == "return_context" else None
		self.request.items = [self.item]
		self.row = Mock(conversion_factor=10, stock_qty=-20)
		self.row.precision.return_value = 6

	def _create_return(self, invoice=False, collected=None):
		self.request.docstatus = 1
		self.request.company = "Company"
		self.request.delivery_note = None if invoice else "DN-1"
		self.request.sales_invoice = "SI-1"
		self.request.credit_note = self.request.return_stock_document = None
		self.request.return_warehouse = "Returns - Company"
		self.framework.get_doc.side_effect = [self.request, SimpleNamespace(name="SOURCE-1")]
		self.row.dn_detail = "DNI-1"
		self.row.sales_invoice_item = "SII-1"
		target = SimpleNamespace(name="RETURN-1", items=[self.row], insert=Mock())
		target.set = lambda key, value: setattr(target, key, value)
		with (
			patch.object(service, "require_document_permission"),
			patch.object(service, "require_company_access"),
			patch.object(service, "require_doctype_permission"),
			patch.object(service, "recalculate_return_request"),
			patch.object(service, "_get_collected_return_stock_quantities", return_value=collected),
			patch("erpnext.controllers.sales_and_purchase_return.make_return_doc", return_value=target),
		):
			result = service.create_return_stock_document("RR-1")
		return result, target

	def test_delivery_note_creation_prefills_actual_collection(self):
		result, target = self._create_return(collected={"DNI-1": 15})
		self.assertEqual(result, {"doctype": "Delivery Note", "name": "RETURN-1"})
		self.assertEqual((target.items[0].qty, target.items[0].stock_qty), (-1.5, -15))
		self.assertEqual(target.items[0].warehouse, "Returns - Company")
		target.insert.assert_called_once()

	def test_invoice_creation_prefills_actual_collection(self):
		result, target = self._create_return(invoice=True, collected={"SII-1": 10})
		self.assertEqual(result, {"doctype": "Sales Invoice", "name": "RETURN-1"})
		self.assertEqual((target.items[0].qty, target.items[0].stock_qty), (-1, -10))

	def test_creation_without_collection_preserves_expected_quantity(self):
		_result, target = self._create_return()
		self.assertEqual((target.items[0].qty, target.items[0].stock_qty), (-2, -20))

	def test_creation_after_failed_pickup_does_not_create_expected_return(self):
		for invoice in (False, True):
			with self.subTest(invoice=invoice), self.assertRaisesRegex(ValueError, "No collected"):
				self._create_return(invoice=invoice, collected={})

	def test_no_submitted_collection_preserves_manual_flow(self):
		self.framework.get_all.return_value = []
		self.assertIsNone(service._get_collected_return_stock_quantities(self.request, "delivery_note_item"))
		self.assertEqual(service._sales_return_quantity(self.row, self.item, None, "delivery_note_item"), 2)
		self.assertEqual(self.framework.get_all.call_args.kwargs["filters"]["docstatus"], 1)

	def test_partial_collection_converts_stock_to_cartons(self):
		self.framework.get_all.side_effect = [["POD-1"], [frappe._dict(source_row="RRI-1", stock_qty=15)]]
		collected = service._get_collected_return_stock_quantities(self.request, "delivery_note_item")
		self.assertEqual(
			service._sales_return_quantity(self.row, self.item, collected, "delivery_note_item"), 1.5
		)
		self.assertEqual(self.item.requested_return_qty, 2)

	def test_zero_and_uncollected_items_do_not_fall_back_to_expected(self):
		self.framework.get_all.side_effect = [["POD-1"], [frappe._dict(source_row="RRI-1", stock_qty=0)]]
		collected = service._get_collected_return_stock_quantities(self.request, "delivery_note_item")
		self.assertEqual(
			service._sales_return_quantity(self.row, self.item, collected, "delivery_note_item"), 0
		)
		self.assertEqual(service._sales_return_quantity(self.row, self.item, {}, "delivery_note_item"), 0)

	def test_multiple_pickups_less_previously_received(self):
		self.item.received_qty = 0.5
		self.framework.get_all.side_effect = [
			["POD-1", "POD-2"],
			[
				frappe._dict(source_row="RRI-1", stock_qty=5),
				frappe._dict(source_row="RRI-1", stock_qty=10),
			],
		]
		collected = service._get_collected_return_stock_quantities(self.request, "delivery_note_item")
		self.assertEqual(
			service._sales_return_quantity(self.row, self.item, collected, "delivery_note_item"), 1
		)

	def test_same_item_code_keeps_distinct_source_rows(self):
		self.request.items.append(frappe._dict(self.item, name="RRI-2", delivery_note_item="DNI-2"))
		self.framework.get_all.side_effect = [["POD-1"], [frappe._dict(source_row="RRI-2", stock_qty=10)]]
		collected = service._get_collected_return_stock_quantities(self.request, "delivery_note_item")
		self.assertEqual(collected["DNI-2"], 10)
		self.assertEqual(collected["DNI-1"], 0)

	def test_stock_invoice_uses_its_own_source_and_mapped_uom(self):
		self.row.conversion_factor = 1
		self.assertEqual(
			service._sales_return_quantity(self.row, self.item, {"SII-1": 15}, "sales_invoice_item"), 15
		)

	def test_mapping_remaining_quantity_and_consumed_allowance_are_respected(self):
		self.row.stock_qty = -5
		collected = {"DNI-1": 15}
		self.assertEqual(
			service._sales_return_quantity(self.row, self.item, collected, "delivery_note_item"), 0.5
		)
		self.assertEqual(collected["DNI-1"], 10)

	def test_invalid_conversion_and_upward_rounding_are_rejected(self):
		self.row.conversion_factor = 0
		with self.assertRaises(ValueError):
			service._sales_return_quantity(self.row, self.item, {"DNI-1": 1}, "delivery_note_item")
		self.row.conversion_factor = 24
		self.row.precision.side_effect = lambda field: 3 if field == "qty" else 6
		with self.assertRaises(ValueError):
			service._sales_return_quantity(self.row, self.item, {"DNI-1": 1}, "delivery_note_item")
