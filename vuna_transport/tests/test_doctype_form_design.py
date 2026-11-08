"""Form layout contracts; no site writes or stock/accounting behavior changes."""

import json
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "vuna_transport" / "doctype"
PRESERVED_CREATION = {
	"return_request": "2026-09-07 07:29:34.478202",
	"transport_load": "2026-08-10 13:16:34.170683",
	"transport_load_item": "2026-08-10 13:14:31.503956",
	"vehicle_allocation": "2026-08-15 11:12:48.674932",
	"transport_trip_task": "2026-11-04 12:30:58.000000",
	"proof_of_delivery": "2026-08-11 12:46:49.514515",
	"proof_of_delivery_item": "2026-08-11 12:40:49.079054",
	"vehicle_onboarding": "2026-09-22 13:30:00",
	"vehicle_onboarding_document": "2026-09-22 13:30:00",
	"return_inspection": "2026-10-28 05:37:24.584050",
	"return_inspection_result": "2026-10-28 08:04:55.941221",
	"transport_route": "2026-08-10 10:06:55.005436",
	"transport_route_stop": "2026-08-10 10:00:05.974493",
	"transport_settings": "2026-08-10 09:58:45.455707",
	"gate_pass": "2026-09-17 09:00:00.000000",
}


def definition(name):
	return json.loads((ROOT / name / (name + ".json")).read_text())


def fields(name):
	return {row["fieldname"]: row for row in definition(name)["fields"]}


class TestDocTypeFormDesign(unittest.TestCase):
	def test_every_field_is_ordered_exactly_once(self):
		for file in ROOT.glob("*/*.json"):
			doc = json.loads(file.read_text())
			if doc.get("doctype") != "DocType":
				continue
			with self.subTest(doctype=doc["name"]):
				names = [row["fieldname"] for row in doc["fields"]]
				self.assertEqual(len(names), len(set(names)))
				self.assertEqual(len(doc["field_order"]), len(set(doc["field_order"])))
				self.assertEqual(set(names), set(doc["field_order"]))

	def test_all_standard_doctypes_have_absolute_audit_metadata(self):
		for file in ROOT.glob("*/*.json"):
			doc = json.loads(file.read_text())
			if doc.get("doctype") != "DocType":
				continue
			with self.subTest(doctype=doc["name"]):
				for key in ("creation", "modified"):
					self.assertRegex(doc[key], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d{1,6})?$")
					datetime.fromisoformat(doc[key])
				self.assertTrue(doc["owner"])
				self.assertTrue(doc["modified_by"])

	def test_known_creation_dates_are_not_replaced_by_redesign_date(self):
		for name, creation in PRESERVED_CREATION.items():
			with self.subTest(doctype=name):
				self.assertEqual(definition(name)["creation"], creation)

	def test_return_request_has_explicit_resolution_and_custody_order(self):
		order = definition("return_request")["field_order"]
		self.assertEqual(order[0], "return_tab")
		self.assertLess(order.index("resolution_tab"), order.index("commercial_resolution"))
		self.assertLess(order.index("commercial_resolution"), order.index("custody_tab"))
		self.assertLess(order.index("custody_events"), order.index("connections_tab"))
		self.assertEqual(fields("return_request")["process_summary"]["hidden"], 1)

	def test_load_contents_and_status_are_on_first_tab(self):
		order = definition("transport_load")["field_order"]
		self.assertLess(order.index("status"), order.index("delivery_orders"))
		self.assertLess(order.index("delivery_orders"), order.index("planning_tab"))
		self.assertLess(order.index("delivery_orders"), order.index("execution_tab"))
		self.assertLess(order.index("execution_tab"), order.index("execution_overview"))

	def test_load_grid_prioritizes_cargo_and_requested_uom(self):
		grid = {key for key, value in fields("transport_load_item").items() if value.get("in_list_view")}
		self.assertEqual(
			grid, {"item_code", "allocated_quantity", "uom", "available_quantity", "destination_warehouse"}
		)
		self.assertEqual(fields("transport_load_item")["uom"]["read_only"], 1)
		self.assertIn("allocated_stock_qty", fields("transport_load_item"))

	def test_receipt_audit_is_secondary_and_contextual(self):
		doc = definition("transport_trip_task")
		self.assertLess(doc["field_order"].index("items"), doc["field_order"].index("confirmation_section"))
		self.assertEqual(
			fields("transport_trip_task")["confirmation_section"]["depends_on"],
			"eval:!!doc.confirmation_method",
		)
		self.assertEqual(fields("transport_trip_task")["direct_assignment"]["hidden"], 1)
		for name in ("receipt_stock_entry", "receipt_purchase_receipt"):
			self.assertIn(name, fields("transport_trip_task")[name]["depends_on"])

	def test_cost_json_is_preserved_but_not_exposed_as_primary_ui(self):
		doc = definition("vehicle_allocation")
		self.assertLess(doc["field_order"].index("loaded_items"), doc["field_order"].index("costs_tab"))
		self.assertEqual(fields("vehicle_allocation")["cost_breakdown"]["fieldtype"], "JSON")
		self.assertEqual(fields("vehicle_allocation")["cost_breakdown"]["hidden"], 1)
		self.assertEqual(fields("vehicle_allocation")["cost_breakdown_preview"]["fieldtype"], "HTML")

	def test_vehicle_and_category_precede_transport_load_selection(self):
		order = definition("vehicle_allocation")["field_order"]
		self.assertLess(order.index("company"), order.index("required_vehicle_category"))
		self.assertLess(order.index("owned_fleet_section"), order.index("required_vehicle_category"))
		self.assertLess(order.index("vehicle"), order.index("required_vehicle_category"))
		self.assertLess(order.index("planned_reporting_time"), order.index("delivery_load_section"))
		self.assertLess(order.index("delivery_load_section"), order.index("delivery_loads"))
		category = fields("vehicle_allocation")["required_vehicle_category"]
		self.assertTrue(category["reqd"])
		self.assertFalse(category.get("hidden"))
		self.assertFalse(fields("vehicle_allocation")["owned_fleet_section"].get("collapsible"))

	def test_handover_identity_precedes_evidence_and_audit(self):
		order = definition("proof_of_delivery")["field_order"]
		self.assertEqual(order[0], "delivery_tab")
		self.assertLess(order.index("delivery_trip"), order.index("items"))
		self.assertLess(order.index("items"), order.index("signature"))
		self.assertLess(order.index("signature"), order.index("recorded_by"))

	def test_draft_inspection_defaults_stay_optional(self):
		for name in ("accepted_warehouse", "rejected_warehouse"):
			self.assertFalse(fields("return_inspection")[name].get("reqd"))
		self.assertEqual(
			fields("return_inspection_result")["target_warehouse"]["mandatory_depends_on"],
			"eval:parent.docstatus == 1",
		)

	def test_route_labels_explain_existing_units_without_converting_values(self):
		for doctype, name in (
			("transport_route", "estimated_duration"),
			("transport_load", "estimated_duration"),
		):
			self.assertEqual(fields(doctype)[name]["fieldtype"], "Float")
			self.assertIn("Hours", fields(doctype)[name]["label"])
		order = definition("transport_route")["field_order"]
		for name in ("section_break_nksm", "section_break_ikvr", "section_break_omyd"):
			self.assertNotIn(name, order)

	def test_onboarding_upload_and_supported_settings_are_visible(self):
		self.assertEqual(fields("vehicle_onboarding_document")["attachment"]["in_list_view"], 1)
		for name in ("enable_carrier_management", "enable_gps_tracking", "enable_route_optimization"):
			self.assertEqual(fields("transport_settings")[name]["hidden"], 1)
		self.assertFalse(fields("transport_settings")["enable_transport_costing"].get("hidden"))
