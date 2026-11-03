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

	def test_global_disable_takes_precedence(self):
		self.configure()
		self.settings.enabled = 0
		self.assertFalse(get_source_policy("Material Request").enabled)

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
			[frappe._dict(source_document_type="Purchase Order")],
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
