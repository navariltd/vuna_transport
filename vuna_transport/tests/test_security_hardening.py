"""Regression coverage for the exposed request and SQL security boundaries."""

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.utils.typing_validations import transform_parameter_types

from vuna_transport import api
from vuna_transport.services.execution import delivery_note_scan_service as scans
from vuna_transport.services.execution import dispatch_guard_service as guards
from vuna_transport.services.execution import return_service as returns
from vuna_transport.services.fleet import fleet_service as fleet
from vuna_transport.services.planning import dispatch_filter_service as filters
from vuna_transport.services.planning import load_validation_service as validation
from vuna_transport.services.shared import security_service as security
from vuna_transport.vuna_transport.report.delivery_trips_pending_pod import delivery_trips_pending_pod
from vuna_transport.vuna_transport.report.vehicle_allocation_history import vehicle_allocation_history
from vuna_transport.vuna_transport.report.vehicle_incident_register import vehicle_incident_register
from vuna_transport.vuna_transport.report.vehicles_in_trip import vehicles_in_trip


class TestSecurityHardening(unittest.TestCase):
	def test_mutating_endpoints_are_post_only_and_not_guest_accessible(self):
		for function in (
			api.bootstrap_mobile_device,
			api.register_mobile_device_push_token,
			api.start_mobile_trip,
			api.report_mobile_incident,
			api.resolve_mobile_incident,
			api.prepare_mobile_pod,
			api.submit_mobile_pod,
			api.upload_mobile_pod_file,
			api.create_mobile_trip_expense_claim,
			api.create_mobile_trip_material_request,
			scans.scan_delivery_note_qr,
			returns.create_return_request_from_delivery_note,
			returns.transition_return_request,
			returns.record_custody_transfer,
			returns.create_return_stock_document,
			returns.record_no_further_action,
			returns.create_replacement_sales_order,
		):
			with self.subTest(function=function.__name__):
				self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[function], ["POST"])
				self.assertNotIn(function, frappe.guest_methods)

	def test_mobile_document_identifiers_reject_structured_values_but_payloads_keep_both_encodings(self):
		function = inspect.unwrap(api.submit_mobile_pod)
		for field in ("installation_id", "trip_name", "stop_name", "device_identifier"):
			with self.subTest(field=field), self.assertRaises(frappe.FrappeTypeError):
				transform_parameter_types(function, (), {field: {"name": "malformed"}})
		for payload in ({"items": []}, '{"items": []}'):
			_, values = transform_parameter_types(function, (), {"payload": payload})
			self.assertEqual(values["payload"], payload)

	def test_all_four_reports_bind_filters_and_hide_unreadable_documents(self):
		malicious = "Example' OR 1=1 --"
		query_type = type(frappe.qb.from_(frappe.qb.DocType("Vehicle Allocation")))

		def run(query, **kwargs):
			if "tabVehicle Allocation Load" in query.get_sql():
				return [
					frappe._dict(parent="allowed", delivery_load=name) for name in ("TL-2", "TL-1", "TL-2")
				]
			return [{"name": "allowed"}, {"name": "restricted"}]

		for report, doctype, fieldnames in (
			(delivery_trips_pending_pod, "Delivery Trip", ("company", "delivery_load")),
			(
				vehicle_allocation_history,
				"Vehicle Allocation",
				("company", "vehicle", "from_date", "to_date"),
			),
			(vehicle_incident_register, "Vehicle Incident", ("company", "vehicle", "resolution_status")),
			(vehicles_in_trip, "Delivery Trip", ("company", "status")),
		):
			with (
				self.subTest(report=report.__name__),
				patch.object(report, "_", side_effect=lambda value: value),
				patch.object(query_type, "run", autospec=True, side_effect=run) as execute,
				patch.object(security, "require_doctype_permission") as permission,
				patch.object(frappe, "get_list", return_value=["allowed"]) as permitted,
			):
				_, rows = report.execute({field: malicious for field in fieldnames})
				self.assertEqual([row["name"] for row in rows], ["allowed"])
				query, params = execute.call_args_list[0].args[0].walk()
				self.assertNotIn(malicious, query)
				self.assertEqual(list(params.values()).count(malicious), len(fieldnames))
				if report is vehicle_allocation_history:
					self.assertEqual(rows[0]["delivery_load"], "TL-2, TL-1")
					child_query, child_params = execute.call_args_list[1].args[0].walk()
					self.assertNotIn("restricted", child_params.values())
					self.assertIn("allowed", child_params.values())
					self.assertIn("ORDER BY", child_query)
				permission.assert_called_once_with(doctype, "read")
				self.assertEqual(permitted.call_args.args, (doctype,))
				self.assertFalse(permitted.call_args.kwargs.get("ignore_permissions", False))

	def test_report_permissions_fail_closed(self):
		with patch.object(security, "require_doctype_permission", side_effect=frappe.PermissionError):
			with self.assertRaises(frappe.PermissionError):
				security.filter_readable_rows("Vehicle Allocation", [{"name": "restricted"}])
		with (
			patch.object(security, "require_doctype_permission"),
			patch.object(frappe, "get_list", side_effect=frappe.PermissionError),
			self.assertRaises(frappe.PermissionError),
		):
			security.filter_readable_rows("Vehicle Allocation", [{"name": "restricted"}])

	def test_asset_search_binds_search_text_and_does_not_interpolate_column_input(self):
		malicious = "name` OR 1=1 --"
		query_type = type(frappe.qb.from_(frappe.qb.DocType("Asset")))
		with (
			patch.object(fleet, "require_doctype_permission"),
			patch.object(
				query_type,
				"run",
				autospec=True,
				return_value=[("allowed", "A", "Company"), ("restricted", "B", "Company")],
			) as execute,
			patch.object(frappe, "get_list", return_value=["allowed"]),
		):
			rows = fleet.get_unlinked_assets("Asset", malicious, "unsupported", 0, 20, {"company": malicious})
			self.assertEqual(rows, [("allowed", "A", "Company")])
			query, params = execute.call_args.args[0].walk()
			self.assertNotIn(malicious, query)
			self.assertNotIn("unsupported", query)
			self.assertIn(malicious, params.values())
			self.assertIn(f"%{malicious}%", params.values())
			self.assertIn("NOT EXISTS", query)
			with self.assertRaises(frappe.DataError):
				fleet.get_unlinked_assets("Asset", "", malicious, 0, 20, {})

	def test_lock_helpers_bind_document_names_and_reject_unknown_tables(self):
		malicious = "name' OR 1=1 --"
		query_type = type(frappe.qb.from_(frappe.qb.DocType("Transport Load")))
		with patch.object(query_type, "run", autospec=True) as execute:
			guards.lock_document("Transport Load", malicious)
			query, params = execute.call_args.args[0].walk()
			self.assertIn("FOR UPDATE", query)
			self.assertNotIn(malicious, query)
			self.assertIn(malicious, params.values())
		with self.assertRaises(ValueError):
			guards.lock_document("Transport Load` DROP TABLE", malicious)
		with patch.object(query_type, "run", autospec=True) as execute:
			validation._lock_delivery_order_items([malicious])
			query, params = execute.call_args.args[0].walk()
			self.assertNotIn(malicious, query)
			self.assertIn(malicious, params.values())
			self.assertIn("ORDER BY", query)
			self.assertIn("FOR UPDATE", query)

	def test_filter_labels_are_translated_per_request_not_shared_between_sites(self):
		with (
			patch.object(
				frappe, "get_list", return_value=[frappe._dict(name="group", field_key="delivery_group")]
			),
			patch.object(filters, "_", side_effect=lambda value: "Site A: " + value),
		):
			first = filters.get_filter_definitions()
		with (
			patch.object(
				frappe, "get_list", return_value=[frappe._dict(name="group", field_key="delivery_group")]
			),
			patch.object(filters, "_", side_effect=lambda value: "Site B: " + value),
		):
			second = filters.get_filter_definitions()
		self.assertEqual(first[0]["label"], "Site A: Delivery Group")
		self.assertEqual(second[0]["label"], "Site B: Delivery Group")

	def test_scan_writes_rely_on_request_transaction_and_never_commit_early(self):
		doc = SimpleNamespace(
			name="DN-1",
			company="Company",
			docstatus=1,
			is_return=0,
			vuna_dispatch_status="Ready for Loading",
			vuna_dispatch_scan_count=0,
		)
		with (
			patch.object(scans, "_require_scan_action_permission"),
			patch.object(scans, "_resolve_qr_token", return_value=doc),
			patch.object(scans, "now_datetime", return_value="2026-11-24 12:00:00"),
			patch.object(scans, "require_document_permission"),
			patch.object(frappe, "has_permission", return_value=True),
			patch.object(frappe, "get_doc") as event,
			patch.object(frappe.db, "set_value"),
			patch.object(frappe.db, "commit") as commit,
		):
			scans.scan_delivery_note_qr("token", "Loaded")
			event.return_value.insert.assert_called_once_with(ignore_permissions=True)
			commit.assert_not_called()
