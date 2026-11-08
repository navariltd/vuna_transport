import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from vuna_transport.services.demand.source_policy_service import (
	automatically_create_from_source,
	get_source_policy,
	require_source_enabled,
)
from vuna_transport.vuna_transport.doctype.transport_settings.transport_settings import TransportSettings


class TestSourcePolicy(unittest.TestCase):
	@patch(
		"vuna_transport.services.demand.sales_transport_demand_service.create_sales_demands",
		return_value=["TD-NATIVE"],
	)
	@patch("vuna_transport.services.demand.delivery_order_service.create_delivery_orders_for_sales_order")
	def test_automatic_sales_generation_uses_native_demand_not_delivery_order(self, legacy, native):
		self.configure()
		row = self.settings.demand_sources[0]
		row.source_document_type, row.creation_mode = "Sales Order", "Automatic"
		source = frappe._dict(doctype="Sales Order", name="SO-1", docstatus=1)
		with patch(
			"vuna_transport.services.demand.sales_transport_demand_service.native_customer_demand_enabled",
			return_value=True,
		):
			self.assertEqual(automatically_create_from_source(source), ["TD-NATIVE"])
		native.assert_called_once_with(source, automated=True)
		legacy.assert_not_called()

	def setUp(self):
		self.framework = self.enterContext(
			patch("vuna_transport.services.demand.source_policy_service.frappe")
		)
		self.framework._dict.side_effect = frappe._dict
		self.settings = frappe._dict(enabled=1, demand_sources=[])
		self.framework.get_cached_doc.return_value = self.settings
		self.enterContext(
			patch("vuna_transport.services.demand.source_policy_service._", side_effect=lambda value: value)
		)
		self.framework.throw.side_effect = ValueError

	def configure(self, **values):
		self.settings.demand_sources = [
			frappe._dict(
				source_document_type="Material Request",
				enabled=1,
				creation_mode="Manual",
				auto_submit_demand=0,
				required_workflow_state=None,
				**values,
			)
		]

	def test_manual_is_enabled_without_automatic_creation(self):
		self.configure()
		self.assertTrue(get_source_policy("Material Request").enabled)
		self.assertEqual(
			automatically_create_from_source(frappe._dict(doctype="Material Request", docstatus=1)), []
		)

	def test_unconfigured_source_is_disabled(self):
		self.configure()
		self.assertFalse(get_source_policy("Sales Order").enabled)
		self.assertFalse(get_source_policy("Purchase Order").enabled)

	def test_supplier_pickup_is_not_enabled_by_legacy_defaults(self):
		self.settings.demand_sources = []
		self.assertFalse(get_source_policy("Purchase Order").enabled)

	@patch("vuna_transport.services.demand.transport_demand_service.build_source_legs", return_value=[{}])
	@patch(
		"vuna_transport.services.demand.transport_demand_service._create_transport_demands",
		return_value=["TD-PO"],
	)
	def test_automatic_purchase_demand_requires_both_opt_in_and_source_policy(self, generate, legs):
		self.enterContext(
			patch(
				"vuna_transport.services.demand.delivery_method_service.get_delivery_method_workflow",
				return_value=frappe._dict(requires_transport_planning=True),
			)
		)
		self.enterContext(
			patch(
				"vuna_transport.services.demand.delivery_method_service.is_company_pickup_order",
				side_effect=lambda doc: doc.get("vuna_delivery_method") == "Company Pickup",
			)
		)
		self.configure()
		row = self.settings.demand_sources[0]
		row.source_document_type, row.creation_mode = "Purchase Order", "Automatic"
		doc = frappe._dict(doctype="Purchase Order", name="PO", docstatus=1, vuna_delivery_method=None)
		self.assertEqual(automatically_create_from_source(doc), [])
		generate.assert_not_called()

		doc.vuna_delivery_method = "Company Pickup"
		self.assertEqual(automatically_create_from_source(doc), ["TD-PO"])
		generate.assert_called_once_with("Purchase Order", "PO", automated=True)
		generate.reset_mock()
		for status in ("On Hold", "Closed", "Completed"):
			doc.status = status
			self.assertEqual(automatically_create_from_source(doc), [])
		generate.assert_not_called()

	def test_company_manual_method_never_automatically_generates_demands(self):
		self.configure()
		for doctype in ("Purchase Order", "Sales Order"):
			row = self.settings.demand_sources[0]
			row.source_document_type, row.creation_mode = doctype, "Automatic"
			with patch(
				"vuna_transport.services.demand.delivery_method_service.get_delivery_method_workflow",
				return_value=frappe._dict(requires_transport_planning=False),
			):
				self.assertEqual(
					automatically_create_from_source(
						frappe._dict(doctype=doctype, docstatus=1, vuna_delivery_method="Manual Company")
					),
					[],
				)

	def test_global_disable_takes_precedence(self):
		self.configure()
		self.settings.enabled = 0
		self.assertFalse(get_source_policy("Material Request").enabled)

	def test_transfer_and_return_manual_methods_do_not_generate_automatically(self):
		self.configure()
		for source_type in ("Material Request", "Return Request"):
			self.settings.demand_sources[0].source_document_type = source_type
			self.settings.demand_sources[0].creation_mode = "Automatic"
			with patch(
				"vuna_transport.services.demand.delivery_method_service.get_source_delivery_method_workflow",
				return_value=frappe._dict(transport_arranged_by="Company", requires_transport_planning=False),
			):
				self.assertEqual(
					automatically_create_from_source(frappe._dict(doctype=source_type, docstatus=1)), []
				)

	def test_counterparty_transfer_does_not_generate_company_demand(self):
		self.configure()
		with (
			patch(
				"vuna_transport.services.demand.delivery_method_service.source_uses_company_transport",
				return_value=False,
			),
			self.assertRaises(ValueError),
		):
			require_source_enabled(frappe._dict(doctype="Material Request", name="MR"))

	@patch(
		"vuna_transport.vuna_transport.doctype.transport_settings.transport_settings._",
		side_effect=lambda value: value,
	)
	@patch(
		"vuna_transport.vuna_transport.doctype.transport_settings.transport_settings.frappe.throw",
		side_effect=ValueError,
	)
	def test_settings_reject_unsupported_and_duplicate_sources(self, throw, translate):
		for rows in (
			[frappe._dict(source_document_type="Purchase Invoice")],
			[
				frappe._dict(source_document_type="Material Request", creation_mode="Manual"),
				frappe._dict(source_document_type="Material Request", creation_mode="Manual"),
			],
		):
			settings = SimpleNamespace(
				load_suggestion_option_count=3, demand_sources=rows, get=lambda key: rows
			)
			with self.assertRaises(ValueError):
				TransportSettings.validate(settings)

	def test_required_approval_state_is_enforced(self):
		self.configure()
		self.settings.demand_sources[0].required_workflow_state = "Approved"
		with self.assertRaises(ValueError):
			require_source_enabled(frappe._dict(doctype="Material Request", workflow_state="Pending"))

	def test_automatic_request_type_is_restricted(self):
		self.configure()
		self.settings.demand_sources[0].creation_mode = "Automatic"
		self.assertEqual(
			automatically_create_from_source(
				frappe._dict(doctype="Material Request", docstatus=1, material_request_type="Purchase")
			),
			[],
		)

	@patch("vuna_transport.services.demand.transport_demand_service.build_source_legs", return_value=[{}])
	@patch(
		"vuna_transport.services.demand.transport_demand_service._create_transport_demands",
		return_value=["TD-1"],
	)
	def test_automatic_generation_uses_trusted_hook_only(self, generate, legs):
		self.enterContext(
			patch(
				"vuna_transport.services.demand.delivery_method_service.get_source_delivery_method_workflow",
				return_value=frappe._dict(transport_arranged_by="Company", requires_transport_planning=True),
			)
		)
		self.configure()
		self.settings.demand_sources[0].creation_mode = "Automatic"
		self.assertEqual(
			automatically_create_from_source(
				frappe._dict(
					doctype="Material Request",
					name="MR-1",
					docstatus=1,
					material_request_type="Material Transfer",
				)
			),
			["TD-1"],
		)
		generate.assert_called_once_with("Material Request", "MR-1", automated=True)
