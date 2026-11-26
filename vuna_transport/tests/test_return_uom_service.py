"""A carton sale can return loose pieces without losing stock or original pricing."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.utils import flt

from vuna_transport.services.execution import return_service as returns
from vuna_transport.services.execution import return_uom_service as service


class Row(SimpleNamespace):
	def get(self, field, default=None):
		return getattr(self, field, default)

	def precision(self, field):
		return 6


class TestReturnUom(unittest.TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(service, "frappe"))
		self.framework._dict = frappe._dict
		self.framework.throw.side_effect = ValueError
		self.framework.db.get_value.return_value = 0
		self.framework.get_cached_doc.return_value = Row(
			variant_of=None, uoms=[Row(uom="Pack", conversion_factor=6)]
		)
		self.enterContext(patch.object(service, "_", side_effect=lambda value: value))
		self.enterContext(
			patch.object(
				service,
				"flt",
				side_effect=lambda value, precision=None: flt(
					value, precision, rounding_method="Banker's Rounding"
				),
			)
		)
		self.enterContext(patch.object(service, "require_document_permission"))
		self.source = Row(item_code="MILK", uom="Carton", stock_uom="Pcs", conversion_factor=12, qty=2)
		self.row = Row(uom="Pcs", requested_return_qty=5, conversion_factor=999)

	def test_pieces_use_stock_factor_not_client_factor(self):
		service.configure_return_uom(self.row, self.source)
		self.assertEqual((self.row.uom, self.row.conversion_factor, self.row.delivered_qty), ("Pcs", 1, 24))
		self.assertEqual(self.row.requested_return_qty, 5)

	def test_original_uom_uses_historical_factor(self):
		self.row.uom, self.row.requested_return_qty = "Carton", 1
		service.configure_return_uom(self.row, self.source)
		self.assertEqual(self.row.conversion_factor, 12)
		self.framework.get_cached_doc.assert_not_called()

	def test_blank_defaults_to_source_uom(self):
		self.row.uom = None
		service.configure_return_uom(self.row, self.source)
		self.assertEqual(self.row.uom, "Carton")

	def test_alternate_uom_requires_item_configuration(self):
		self.row.uom = "Pack"
		service.configure_return_uom(self.row, self.source)
		self.assertEqual((self.row.conversion_factor, self.row.delivered_qty), (6, 4))
		self.row.uom = "Litre"
		with self.assertRaises(ValueError):
			service.configure_return_uom(self.row, self.source)

	def test_variant_can_use_template_conversion(self):
		self.framework.get_cached_doc.side_effect = [
			Row(variant_of="TEMPLATE", uoms=[]),
			Row(uoms=[Row(uom="Pack", conversion_factor=6)]),
		]
		self.assertEqual(service.return_conversion_factor(self.source, "Pack"), 6)

	def test_nonfinite_or_nonpositive_factors_rejected(self):
		for factor in (0, -1, float("nan"), float("inf")):
			self.source.conversion_factor = factor
			with self.subTest(factor=factor), self.assertRaises(ValueError):
				service.return_conversion_factor(self.source, "Carton")

	def test_nonfinite_or_nonpositive_quantities_rejected(self):
		for quantity in (0, -1, float("nan"), float("inf")):
			self.row.requested_return_qty = quantity
			with self.subTest(quantity=quantity), self.assertRaises(ValueError):
				service.configure_return_uom(self.row, self.source)

	def test_whole_stock_units_cannot_be_fractional(self):
		self.framework.db.get_value.return_value = 1
		self.row.requested_return_qty = 0.5
		with self.assertRaises(ValueError):
			service.configure_return_uom(self.row, self.source)

	def test_quantity_rounding_cannot_create_fractional_pieces(self):
		self.framework.db.get_value.side_effect = lambda doctype, name, field: int(name == "Pcs")
		self.row.uom, self.row.requested_return_qty = "Pack", 5 / 6
		with self.assertRaises(ValueError):
			service.configure_return_uom(self.row, self.source)

	def test_manual_depot_delivered_quantity_converts_to_selected_unit(self):
		self.source.delivered_qty = 3
		service.configure_return_uom(self.row, self.source)
		self.assertEqual(self.row.delivered_qty, 36)

	def test_approved_snapshot_survives_item_conversion_change(self):
		self.row.doctype, self.row.docstatus = "Return Request Item", 1
		self.row.parent, self.row.name = "RR", "RRI"
		self.row.uom, self.row.conversion_factor = "Pack", 4
		self.framework.db.get_value.side_effect = [
			1,
			Row(uom="Pack", stock_uom="Pcs", conversion_factor=4),
			0,
			0,
		]
		service.configure_return_uom(self.row, self.source)
		self.assertEqual(self.row.conversion_factor, 4)
		self.framework.get_cached_doc.assert_not_called()

	def test_approved_uom_cannot_change(self):
		self.row.doctype, self.row.docstatus = "Return Request Item", 1
		self.row.parent, self.row.name = "RR", "RRI"
		self.framework.db.get_value.side_effect = [
			1,
			Row(uom="Carton", stock_uom="Pcs", conversion_factor=12),
		]
		with self.assertRaises(ValueError):
			service.configure_return_uom(self.row, self.source)

	def test_sales_return_keeps_original_price_per_stock_unit(self):
		mapped = Row(
			uom="Carton",
			stock_uom="Pcs",
			conversion_factor=12,
			stock_qty=-24,
			rate=1200,
			base_rate=1200,
			price_list_rate=1500,
			discount_amount=300,
			discount_percentage=20,
			margin_type="Amount",
			margin_rate_or_amount=120,
		)
		self.row.stock_uom, self.row.conversion_factor = "Pcs", 1
		service.apply_sales_return_uom(mapped, self.row, returns.RETURN_SALES_ITEM_COMMERCIAL_FIELDS)
		self.assertEqual((mapped.uom, mapped.conversion_factor, mapped.rate), ("Pcs", 1, 100))
		self.assertEqual(
			(mapped.price_list_rate, mapped.discount_amount, mapped.discount_percentage), (125, 25, 20)
		)
		self.assertEqual(mapped.margin_rate_or_amount, 10)
		self.assertEqual(mapped.stock_qty, -24)

	def test_percentage_margin_is_not_converted(self):
		row = Row(conversion_factor=1)
		source = frappe._dict(conversion_factor=12, margin_type="Percentage", margin_rate_or_amount=10)
		service.copy_return_commercial_terms(row, source, returns.RETURN_SALES_ITEM_COMMERCIAL_FIELDS)
		self.assertEqual(row.margin_rate_or_amount, 10)

	def test_uom_details_checks_source_parent_permission(self):
		self.source.parent = "DN"
		parent = Row(name="DN")
		self.framework.get_doc.side_effect = [self.source, parent]
		result = service.get_return_uom_details("Delivery Note Item", "DNI", "Pcs")
		service.require_document_permission.assert_called_once_with(parent, "read")
		self.assertEqual(result, {"conversion_factor": 1, "stock_uom": "Pcs"})

	def test_arbitrary_source_doctype_rejected(self):
		with self.assertRaises(ValueError):
			service.get_return_uom_details("User", "Administrator", "Pcs")


class TestMixedReturnReservations(unittest.TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(returns, "frappe"))
		self.framework.db.get_value.side_effect = (
			lambda doctype, *args, **kwargs: frappe._dict(qty=2, conversion_factor=12)
			if doctype != "Return Request"
			else None
		)
		self.framework.get_all.return_value = []
		self.query = MagicMock()
		for method in ("join", "on", "select", "where"):
			getattr(self.query, method).return_value = self.query
		self.framework.qb.from_.return_value = self.query
		self.enterContext(
			patch.object(
				returns,
				"flt",
				side_effect=lambda value, precision=None: flt(
					value, precision, rounding_method="Banker's Rounding"
				),
			)
		)

	def remaining(self, posted, reservations, invoice=False):
		self.query.run.side_effect = [
			posted,
			[frappe._dict(return_stock_document=None, credit_note=None, **row) for row in reservations],
		]
		method = (
			returns.get_remaining_returnable_sales_invoice_quantity
			if invoice
			else returns.get_remaining_returnable_quantity
		)
		return method("SOURCE", "CURRENT") * 12

	def test_piece_and_carton_requests_reserve_stock_units(self):
		self.assertEqual(
			self.remaining(
				[],
				[
					{"parent": "RR1", "requested_return_qty": 5, "conversion_factor": 1},
					{"parent": "RR2", "requested_return_qty": 1, "conversion_factor": 12},
				],
			),
			7,
		)

	def test_submitted_partial_sales_return_is_not_reserved_twice(self):
		self.framework.get_all.return_value = [frappe._dict(name="SR", vuna_return_request="RR1")]
		self.assertEqual(
			self.remaining(
				[frappe._dict(parent="SR", stock_qty=-3)],
				[{"parent": "RR1", "requested_return_qty": 5, "conversion_factor": 1}],
			),
			19,
		)

	def test_multiple_partial_receipts_are_deducted_from_one_reservation(self):
		self.framework.get_all.return_value = [
			frappe._dict(name=name, vuna_return_request="RR1") for name in ("SR1", "SR2")
		]
		self.assertEqual(
			self.remaining(
				[frappe._dict(parent=name, stock_qty=-2) for name in ("SR1", "SR2")],
				[{"parent": "RR1", "requested_return_qty": 5, "conversion_factor": 1}],
			),
			19,
		)

	def test_credit_note_does_not_double_count_its_request(self):
		self.query.run.side_effect = [
			[frappe._dict(parent="CREDIT", stock_qty=-5)],
			[frappe._dict(parent="RR1", requested_return_qty=5, conversion_factor=1, credit_note="CREDIT")],
		]
		self.assertEqual(returns.get_remaining_returnable_sales_invoice_quantity("SOURCE") * 12, 19)

	def test_unlinked_return_still_reduces_stock_allowance(self):
		self.assertEqual(self.remaining([frappe._dict(parent="EXTERNAL", stock_qty=-5)], []), 19)

	def test_revalidating_own_request_keeps_its_already_received_allowance(self):
		self.framework.get_all.return_value = [frappe._dict(name="SR", vuna_return_request="CURRENT")]
		self.assertEqual(self.remaining([frappe._dict(parent="SR", stock_qty=-24)], []), 24)

	def test_reservations_never_make_negative_availability(self):
		self.assertEqual(
			self.remaining([], [{"parent": "RR1", "requested_return_qty": 50, "conversion_factor": 1}]), 0
		)
