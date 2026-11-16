"""Operational navigation stays discoverable across sidebar generations."""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "vuna_transport" / "sidebar"
WORKSPACES = ROOT / "vuna_transport" / "workspace"
LEGACY = ROOT / "workspace_sidebar"


def read(path):
	return json.loads(path.read_text())


def sidebar(name, legacy=False):
	return read(LEGACY / f"{name}.json" if legacy else CURRENT / name / f"{name}.json")


def workspace(name):
	return read(WORKSPACES / name / f"{name}.json")


def targets(doc):
	return {
		(row["link_type"], row["link_to"])
		for row in doc.get("items", doc.get("links", []))
		if row.get("type") == "Link"
	}


def section_targets(doc, label):
	result = set()
	current = None
	for row in doc.get("items", doc.get("links", [])):
		if row["type"] in ("Section Break", "Card Break"):
			current = row["label"]
		elif row["type"] == "Link" and current == label:
			result.add(row["link_to"])
	return result


class TestTransportNavigation(unittest.TestCase):
	def test_dispatch_surfaces_include_operational_and_source_documents(self):
		required = {
			"Return Inspection",
			"Shipment Diversion",
			"Transport Trip Task",
			"Dispatch Planning Filter",
			"County",
			"Constituency",
			"Town",
			"Material Request",
			"Stock Entry",
			"Purchase Order",
			"Purchase Receipt",
		}
		for doc in (
			sidebar("dispatch_operations"),
			sidebar("dispatch_operations", legacy=True),
			workspace("dispatch_operations"),
			workspace("vuna_transport"),
		):
			with self.subTest(surface=doc["doctype"], name=doc["name"]):
				self.assertTrue({("DocType", name) for name in required}.issubset(targets(doc)))

	def test_dispatch_groups_follow_the_execution_flow(self):
		for doc in (
			sidebar("dispatch_operations"),
			sidebar("dispatch_operations", legacy=True),
			workspace("dispatch_operations"),
		):
			self.assertIn("Return Inspection", section_targets(doc, "Returns & Resolution"))
			self.assertIn("Shipment Diversion", section_targets(doc, "Returns & Resolution"))
			self.assertIn("Transport Trip Task", section_targets(doc, "Execution"))
			self.assertIn("Dispatch Planning Filter", section_targets(doc, "Dispatch Setup"))
			self.assertEqual(
				section_targets(doc, "Supplier Collections"), {"Purchase Order", "Purchase Receipt"}
			)
			self.assertEqual(section_targets(doc, "Location Setup"), {"County", "Constituency", "Town"})

	def test_fleet_current_legacy_and_workspace_have_the_same_targets(self):
		current = sidebar("fleet_management")
		legacy = sidebar("fleet_management", legacy=True)
		page = workspace("fleet_management")
		# Home is a sidebar link to the workspace itself, not a workspace card.
		current_targets = {row for row in targets(current) if row[0] != "Workspace"}
		self.assertEqual(current_targets, {row for row in targets(legacy) if row[0] != "Workspace"})
		self.assertEqual(current_targets, targets(page))
		self.assertIn(("Report", "Vehicle Allocation History"), current_targets)
		self.assertTrue(
			{"Vehicle Allocation", "Delivery Trip"}.issubset(section_targets(current, "Fleet Operations"))
		)

	def test_diversion_workspace_links_belong_to_the_returns_card_with_correct_counts(self):
		for name in ("dispatch_operations", "vuna_transport"):
			doc = workspace(name)
			with self.subTest(workspace=name):
				links = section_targets(doc, "Returns & Resolution")
				self.assertIn("Shipment Diversion", links)
				card = next(
					row
					for row in doc["links"]
					if row["type"] == "Card Break" and row["label"] == "Returns & Resolution"
				)
				self.assertEqual(card["link_count"], len(links))

	def test_workspace_cards_actually_render_every_link_group(self):
		for name in ("dispatch_operations", "fleet_management", "vuna_transport"):
			doc = workspace(name)
			groups = {row["label"] for row in doc["links"] if row["type"] == "Card Break"}
			cards = [row["data"]["card_name"] for row in json.loads(doc["content"]) if row["type"] == "card"]
			with self.subTest(workspace=name):
				self.assertEqual(groups, set(cards))
				self.assertEqual(len(cards), len(set(cards)))

	def test_navigation_has_no_duplicate_links_or_excluded_mobile_devices(self):
		for doc in (
			sidebar("dispatch_operations"),
			sidebar("fleet_management"),
			sidebar("dispatch_operations", True),
			sidebar("fleet_management", True),
			workspace("dispatch_operations"),
			workspace("fleet_management"),
			workspace("vuna_transport"),
		):
			links = [row for row in doc.get("items", doc.get("links", [])) if row["type"] == "Link"]
			self.assertEqual(len(links), len(targets(doc)))
			self.assertNotIn(("DocType", "Transport Mobile Device"), targets(doc))
			self.assertNotIn(("DocType", "Delivery Order"), targets(doc))

	def test_every_app_report_is_discoverable_in_current_sidebars(self):
		linked = targets(sidebar("dispatch_operations")) | targets(sidebar("fleet_management"))
		for file in (ROOT / "vuna_transport" / "report").glob("*/*.json"):
			report = read(file)
			if not report.get("disabled"):
				self.assertIn(("Report", report["name"]), linked)

	def test_workspace_script_reports_open_the_query_report_route(self):
		for name in ("dispatch_operations", "fleet_management", "vuna_transport"):
			for link in workspace(name)["links"]:
				if link["type"] == "Link" and link["link_type"] == "Report":
					self.assertEqual(link["is_query_report"], 1)

	def test_operational_doctypes_are_linked_without_child_tables_or_audit_logs(self):
		linked = targets(sidebar("dispatch_operations")) | targets(sidebar("fleet_management"))
		for file in (ROOT / "vuna_transport" / "doctype").glob("*/*.json"):
			doc = read(file)
			if doc.get("istable") or doc["name"] in {
				"Transport Mobile Device",
				"Delivery Note Dispatch Event",
			}:
				continue
			self.assertIn(("DocType", doc["name"]), linked)

	def test_existing_workspace_role_restrictions_are_preserved(self):
		for name in ("dispatch_operations", "fleet_management", "vuna_transport"):
			self.assertEqual(
				{row["role"] for row in workspace(name)["roles"]},
				{"System Manager", "Transport Planner", "Transport Dispatcher", "Transport Manager"},
			)
