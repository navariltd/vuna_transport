import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from vuna_transport.services.demand import shipping_address_repair as repair


class TestShippingAddressRepair(unittest.TestCase):
	def setUp(self):
		self.key = ("2026-11-10", "Billing", "Warehouse", "Zone", None, None, "Company Delivery", None)
		self.source = SimpleNamespace(
			name="SO-1",
			shipping_address_name="Shipping",
			customer_address="Billing",
			items=[frappe._dict(name="SOI-1")],
			as_dict=lambda: {
				"name": "SO-1",
				"shipping_address_name": "Shipping",
				"customer_address": "Billing",
			},
		)
		self.demand = SimpleNamespace(
			name="TD-1",
			source_document="SO-1",
			items=[frappe._dict(source_item="SOI-1")],
			destination_address="Billing",
			source_key=repair.sales.sales_source_key("SO-1", self.key, ["SOI-1"]),
		)
		self.group = self.enterContext(patch.object(repair.grouping, "_get_group_key", return_value=self.key))
		self.legs = self.enterContext(
			patch.object(
				repair.sales,
				"build_sales_legs",
				return_value=[
					{
						"source_key": "shipping-key",
						"destination_address": "Shipping",
						"items": [{"source_item": "SOI-1"}],
						"status": "Open",
						"total_weight": 999,
					}
				],
			)
		)

	def test_proven_billing_first_snapshot_changes_only_address_fields_and_key(self):
		values = repair.repair_values(self.demand, self.source)
		self.assertEqual(values["destination_address"], "Shipping")
		self.assertEqual(values["source_key"], "shipping-key")
		self.assertNotIn("items", values)
		self.assertNotIn("status", values)
		self.assertNotIn("total_weight", values)
		self.assertEqual(self.source.shipping_address_name, "Shipping")
		self.assertIsNone(self.group.call_args.args[0].shipping_address_name)

	def test_unrelated_grouping_changes_cannot_be_repaired(self):
		self.demand.source_key = "unrelated-key"
		self.assertIsNone(repair.repair_values(self.demand, self.source))
		self.legs.assert_not_called()

	def test_missing_source_rows_and_changed_row_sets_cannot_be_repaired(self):
		self.source.items = []
		self.assertIsNone(repair.repair_values(self.demand, self.source))
		self.source.items = [frappe._dict(name="SOI-1")]
		self.legs.return_value[0]["items"] = [{"source_item": "different"}]
		self.assertIsNone(repair.repair_values(self.demand, self.source))

	def test_migration_dry_run_and_repeat_are_safe_and_used_demands_are_not_changed(self):
		for dry_run, used, duplicate, stale in (
			(True, False, False, True),
			(False, False, False, True),
			(False, True, False, True),
			(False, False, True, True),
			(False, False, False, False),
		):
			with (
				self.subTest(dry_run=dry_run, used=used, duplicate=duplicate, stale=stale),
				patch.object(repair, "frappe") as framework,
				patch.object(repair, "available_items") as available,
				patch.object(repair, "repair_values", return_value={"source_key": "new-key"}) as values,
			):
				framework.ValidationError = frappe.ValidationError
				framework.get_all.return_value = ["TD-1"]
				framework.get_doc.side_effect = [
					self.demand,
					SimpleNamespace(docstatus=1, status="To Deliver"),
				]
				framework.db.exists.side_effect = [used, duplicate] if not used else [True]
				if stale:
					available.side_effect = repair.CustomerDemandGroupingChanged("stale")
				report = repair.repair_shipping_address_snapshots(dry_run=dry_run)
				if stale and not used and not duplicate:
					self.assertEqual(report["repairable"], ["TD-1"])
					if not dry_run:
						framework.db.set_value.assert_called_once_with(
							"Transport Demand", "TD-1", {"source_key": "new-key"}
						)
						self.assertEqual(report["repaired"], ["TD-1"])
					else:
						framework.db.set_value.assert_not_called()
				else:
					framework.db.set_value.assert_not_called()
					if used or not stale:
						values.assert_not_called()
					if stale:
						self.assertEqual(report["needs_review"], ["TD-1"])
