"""Historical review must never guess conversions or mutate business records."""

import copy
import unittest
from unittest.mock import patch

import frappe

from vuna_transport.services.demand import delivery_order_cutover_audit as audit
from vuna_transport.tests.test_delivery_order_migration import source_fixture


class TestCutoverReviewProjection(unittest.TestCase):
	def setUp(self):
		self.snapshot = source_fixture()
		self.sales = {
			"name": "SO-1",
			"company": "Dairy",
			"customer": "Customer",
			"docstatus": 1,
			"status": "To Deliver and Bill",
			"items": [
				{
					"name": "so-row-1",
					"item_code": "MILK",
					"qty": 3,
					"uom": "Carton",
					"conversion_factor": 10,
					"stock_uom": "Pcs",
					"stock_qty": 30,
				}
			],
		}
		self.groups = {"so-row-1": ("2026-01-01", "Address", "Main", None)}

	def inspect(self):
		return audit.inspect_source(self.snapshot, self.sales, self.groups)

	def test_clean_projection_preserves_original_evidence_and_native_key(self):
		before = copy.deepcopy((self.snapshot, self.sales, self.groups))
		first = self.inspect()
		self.assertFalse(first["issues"])
		self.assertTrue(first["native_source_key"])
		self.assertEqual(first, self.inspect())
		self.assertEqual((self.snapshot, self.sales, self.groups), before)

	def test_missing_uom_has_exact_source_proposal_requiring_approval(self):
		self.snapshot["items"][0].update(uom=None, conversion_factor=0, stock_qty=0)
		case = self.inspect()
		self.assertEqual(case["issues"][0]["code"], "invalid_historical_quantity")
		self.assertEqual(
			case["items"][0]["review_proposal"],
			{
				"uom": "Carton",
				"conversion_factor": 10,
				"stock_uom": "Pcs",
				"stock_qty": 20,
				"approval_required": True,
			},
		)
		self.assertIsNone(self.snapshot["items"][0]["uom"])
		self.assertEqual(self.snapshot["items"][0]["stock_qty"], 0)

	def test_existing_conflicting_conversion_is_not_overwritten(self):
		self.snapshot["items"][0].update(conversion_factor=5, stock_qty=10)
		case = self.inspect()
		self.assertIn("source_uom_changed", [issue["code"] for issue in case["issues"]])
		self.assertNotIn("review_proposal", case["items"][0])

	def test_invalid_source_never_supplies_a_recovery_proposal(self):
		self.snapshot["items"][0].update(uom=None, conversion_factor=0, stock_qty=0)
		self.sales["items"][0]["conversion_factor"] = 0
		case = self.inspect()
		self.assertIn("invalid_source_quantity", [issue["code"] for issue in case["issues"]])
		self.assertNotIn("review_proposal", case["items"][0])

	def test_negative_nonfinite_and_over_source_quantities_are_not_proposed(self):
		for qty in (-1, float("nan"), float("inf"), 4):
			with self.subTest(qty=qty):
				self.snapshot["items"][0].update(
					transport_qty=qty, uom=None, conversion_factor=0, stock_qty=0
				)
				self.assertNotIn("review_proposal", self.inspect()["items"][0])

	def test_missing_source_row_cannot_be_matched_by_item_code(self):
		self.sales["items"][0]["name"] = "same-item-different-row"
		case = self.inspect()
		self.assertIn("source_item_mismatch", [issue["code"] for issue in case["issues"]])
		self.assertIsNone(case["native_source_key"])

	def test_multiple_groups_require_review_not_one_invented_source_key(self):
		second = copy.deepcopy(self.snapshot["items"][0])
		second.update(name="old-row-2", sales_order_item="so-row-2")
		self.snapshot["items"].append(second)
		original = copy.deepcopy(self.sales["items"][0])
		original["name"] = "so-row-2"
		self.sales["items"].append(original)
		self.groups["so-row-2"] = ("2026-01-02", "Other Address", "Main", None)
		case = self.inspect()
		self.assertEqual(len(case["source_groups"]), 2)
		self.assertIsNone(case["native_source_key"])
		self.assertIn("source_group_split_required", [issue["code"] for issue in case["issues"]])

	def test_completed_source_does_not_erase_historical_quantity(self):
		self.sales["status"] = "Completed"
		case = self.inspect()
		self.assertIn("inactive_source", [issue["code"] for issue in case["issues"]])
		self.assertEqual(case["items"][0]["historical_quantity"]["transport_qty"], 2)

	def test_wrong_company_or_customer_requires_review(self):
		self.sales["company"] = "Other Company"
		self.assertIn("source_party_mismatch", [issue["code"] for issue in self.inspect()["issues"]])

	def test_missing_sales_order_requires_review(self):
		self.assertEqual(
			audit.inspect_source(self.snapshot, None, {})["issues"], [{"code": "missing_sales_order"}]
		)


class TestCutoverAuditSafety(unittest.TestCase):
	@patch.object(audit.preparation, "_authorize", side_effect=frappe.PermissionError)
	@patch.object(audit.preparation, "prepare")
	def test_authorization_precedes_any_inventory_read(self, prepare, authorize):
		with self.assertRaises(frappe.PermissionError):
			audit.audit()
		prepare.assert_not_called()

	@patch.object(audit.preparation, "_authorize")
	@patch.object(audit.preparation, "prepare", return_value={"mapping": {}, "blockers": []})
	@patch.object(audit.preparation, "_fingerprint", return_value={"count": 0, "sha256": "unchanged"})
	def test_empty_audit_is_read_only_and_not_activation_authority(self, fingerprint, prepare, authorize):
		with (
			patch.object(audit.frappe.db, "set_value") as write,
			patch.object(audit.frappe.db, "commit") as commit,
		):
			report = audit.audit(source_names=[])
			self.assertFalse(report["cutover_ready"])
			self.assertFalse(report["site_setting_changed"])
			write.assert_not_called()
			commit.assert_not_called()
