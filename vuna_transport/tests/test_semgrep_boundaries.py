"""API type coercion and parameter binding for the hardened PR boundaries."""

import inspect
import unittest
from unittest.mock import patch

import frappe
from frappe.utils.typing_validations import transform_parameter_types

from vuna_transport.api import get_delivery_loads_for_allocation
from vuna_transport.services.execution import lineage_service, return_service
from vuna_transport.services.integrations import erpnext_integration
from vuna_transport.vuna_transport.report.delivery_notes_pending_submission import (
	delivery_notes_pending_submission,
)
from vuna_transport.vuna_transport.report.dispatch_readiness import dispatch_readiness
from vuna_transport.vuna_transport.report.loads_awaiting_vehicle import loads_awaiting_vehicle


class TestSemgrepBoundaries(unittest.TestCase):
	def setUp(self):
		# Keep translation-cache database reads outside query-boundary assertions.
		for report in (loads_awaiting_vehicle, dispatch_readiness, delivery_notes_pending_submission):
			self.enterContext(patch.object(report, "_", side_effect=lambda value: value))

	def test_search_api_preserves_json_filters_and_coerces_pagination(self):
		function = inspect.unwrap(get_delivery_loads_for_allocation)
		_, values = transform_parameter_types(
			function,
			(),
			{
				"doctype": "Transport Load",
				"txt": "",
				"searchfield": "name",
				"start": "0",
				"page_len": "20",
				"filters": '{"company":"Example"}',
				"as_dict": 1,
			},
		)
		self.assertEqual((values["start"], values["page_len"]), (0, 20))
		self.assertEqual(values["filters"], '{"company":"Example"}')
		self.assertTrue(values["as_dict"])

	def test_search_api_accepts_dictionary_filters(self):
		function = inspect.unwrap(get_delivery_loads_for_allocation)
		_, values = transform_parameter_types(function, (), {"filters": {"company": "Example"}})
		self.assertEqual(values["filters"], {"company": "Example"})

	def test_document_name_api_rejects_structured_payload(self):
		function = inspect.unwrap(return_service.create_return_stock_document)
		with self.assertRaises(frappe.FrappeTypeError):
			transform_parameter_types(function, (), {"return_request": {"name": "RR-1"}})

	def test_delivery_note_queries_bind_optional_status_and_load_names(self):
		value = "TL' OR 1=1 --"
		for function in (
			lineage_service.get_delivery_notes_for_loads,
			erpnext_integration._get_delivery_notes_for_loads,
		):
			for status in (None, 0, 1):
				with (
					self.subTest(function=function.__name__, status=status),
					patch.object(frappe.db, "sql", return_value=[]) as sql,
				):
					self.assertEqual(function([value], docstatus=status), [])
					query, parameters = sql.call_args.args
					self.assertNotIn(value, query)
					self.assertEqual(parameters, {"load_names": (value,), "docstatus": status})

	def test_existing_note_query_binds_values_in_both_link_dialects(self):
		value = "TD' OR 1=1 --"
		query_type = type(frappe.qb.from_(frappe.qb.DocType("Delivery Note")))
		for native, column in ((False, "delivery_order"), (True, "transport_demand")):
			with (
				self.subTest(native=native),
				patch.object(frappe.db, "exists", return_value=False),
				patch.object(query_type, "run", autospec=True, return_value=[]) as run,
			):
				self.assertFalse(erpnext_integration._delivery_order_has_existing_note("TL-1", value, native))
				query, parameters = run.call_args.args[0].walk()
				self.assertIn(column, query)
				self.assertNotIn(value, query)
				self.assertIn(value, parameters.values())

	def test_dispatch_report_binds_all_optional_filters(self):
		value = "Example' OR 1=1 --"
		with patch.object(frappe.db, "sql", return_value=[]) as sql:
			dispatch_readiness.execute(
				{"company": value, "status": "Open", "from_date": "2026-01-01", "to_date": "2026-12-31"}
			)
			query, parameters = sql.call_args.args
			self.assertNotIn(value, query)
			self.assertEqual(parameters["company"], value)
			self.assertEqual(parameters["from_date"], "2026-01-01")
			self.assertEqual(parameters["to_date"], "2026-12-31")

	def test_pending_delivery_notes_bind_company_and_load_filters(self):
		with patch.object(frappe.db, "sql", return_value=[]) as sql:
			delivery_notes_pending_submission.execute({"company": "Example", "delivery_load": "TL-1"})
			self.assertEqual(sql.call_args.args[1], {"company": "Example", "delivery_load": "TL-1"})

	def test_awaiting_vehicle_report_cannot_override_fixed_scope(self):
		with (
			patch.object(frappe, "get_list", return_value=[]) as query,
			patch.object(frappe, "get_all") as unrestricted_query,
		):
			loads_awaiting_vehicle.execute(
				{
					"company": "Example",
					"status": "Open",
					"docstatus": 0,
					"active_vehicle_allocation": ["is", "set"],
					"ignore_permissions": True,
					"limit": 10000,
				}
			)
			self.assertEqual(query.call_args.args, ("Transport Load",))
			self.assertEqual(
				query.call_args.kwargs["filters"],
				{
					"company": "Example",
					"status": ["in", ["Approved", "Awaiting Vehicle"]],
					"docstatus": 1,
					"active_vehicle_allocation": ["is", "not set"],
				},
			)
			self.assertEqual(query.call_args.kwargs["limit"], 500)
			self.assertFalse(query.call_args.kwargs.get("ignore_permissions", False))
			unrestricted_query.assert_not_called()

	def test_awaiting_vehicle_report_does_not_bypass_permission_denials(self):
		with (
			patch.object(frappe, "get_list", side_effect=frappe.PermissionError),
			patch.object(frappe, "get_all") as unrestricted_query,
			self.assertRaises(frappe.PermissionError),
		):
			loads_awaiting_vehicle.execute({"company": "Example"})
		unrestricted_query.assert_not_called()
