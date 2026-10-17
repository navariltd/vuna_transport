# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests import UnitTestCase

from vuna_transport.overrides.quality_inspection import (
	ReturnQualityInspectionMixin,
	_normalize_return_inspection_item,
	make_quality_inspections,
)
from vuna_transport.services.execution.return_service import prepare_quality_inspection_removal


class TestReturnQualityInspection(UnitTestCase):
	@patch("vuna_transport.overrides.quality_inspection.frappe.db.get_value")
	def test_submitted_vuna_return_request_authorizes_return_inspection(self, get_value):
		get_value.side_effect = [1, "RR-0001"]
		inspection = SimpleNamespace(
			reference_type="Delivery Note",
			reference_name="DN-RETURN-1",
		)

		self.assertTrue(ReturnQualityInspectionMixin._is_vuna_return_inspection(inspection))

	@patch("vuna_transport.overrides.quality_inspection.frappe.db.get_value", return_value=0)
	def test_normal_delivery_note_does_not_bypass_erpnext_policy(self, _get_value):
		inspection = SimpleNamespace(
			reference_type="Delivery Note",
			reference_name="DN-1",
		)

		self.assertFalse(ReturnQualityInspectionMixin._is_vuna_return_inspection(inspection))

	def test_sales_return_quantities_are_positive_for_erpnext_dialog(self):
		item = _normalize_return_inspection_item(
			{"qty": -500, "stock_qty": -1000, "sample_quantity": -25, "item_code": "ITEM-1"}
		)

		self.assertEqual(item["qty"], 500)
		self.assertEqual(item["stock_qty"], 1000)
		self.assertEqual(item["sample_quantity"], 25)
		self.assertEqual(item["item_code"], "ITEM-1")

	@patch("vuna_transport.overrides.quality_inspection._link_inspections_to_return_request")
	@patch(
		"vuna_transport.overrides.quality_inspection.erpnext_make_quality_inspections",
		return_value=["QI-0001"],
	)
	@patch(
		"vuna_transport.overrides.quality_inspection._get_vuna_return_request",
		return_value="RR-0001",
	)
	def test_sales_return_reuses_erpnext_creator_as_incoming_inspection(
		self, _get_request, erpnext_creator, link_inspections
	):
		result = make_quality_inspections(
			"Test Company",
			"Delivery Note",
			"DN-RETURN-1",
			[{"qty": -500, "sample_size": 50, "item_code": "ITEM-1"}],
			"Outgoing",
		)

		self.assertEqual(result, ["QI-0001"])
		args = erpnext_creator.call_args.args
		self.assertEqual(args[3][0]["qty"], 500)
		self.assertEqual(args[3][0]["sample_size"], 50)
		self.assertEqual(args[4], "Incoming")
		link_inspections.assert_called_once_with("RR-0001", "DN-RETURN-1", ["QI-0001"])

	@patch("vuna_transport.services.execution.return_service.recalculate_return_request")
	@patch("vuna_transport.services.execution.return_service.frappe.db.set_value")
	@patch(
		"vuna_transport.services.execution.return_service._get_return_request_item_for_quality_inspection",
		return_value=SimpleNamespace(name="RRI-0001", parent="RR-0001"),
	)
	def test_deleting_draft_inspection_releases_return_request_backlink(
		self, _get_request_item, set_value, recalculate
	):
		prepare_quality_inspection_removal(SimpleNamespace(name="QI-0001"))

		set_value.assert_called_once_with(
			"Return Request Item",
			"RRI-0001",
			"quality_inspection",
			None,
			update_modified=False,
		)
		recalculate.assert_called_once_with("RR-0001")
