"""Local historical promotion rehearsal; all data changes are rolled back."""

from unittest.mock import patch

import frappe

from vuna_transport.services.demand import delivery_order_migration as preparation
from vuna_transport.services.demand import delivery_order_retirement as retirement


def run(retire_types=False, verify_execution=False):
	report = retirement.inspect()
	assert not report["blockers"], report["blockers"]
	tracked = (
		set(preparation.LEDGERS)
		| set(retirement.RETIRED_TYPES)
		| {
			"Transport Demand",
			"Transport Demand Item",
			"Transport Load",
			"Transport Load Item",
			"Vehicle Allocation",
			"Vehicle Allocation Load",
			"Sales Order",
			"Sales Order Item",
			"DocField",
			"Custom Field",
			"DocType",
			"DocPerm",
			"DocType Link",
			"Report",
			"Report Filter",
			"Has Role",
		}
		| {link[0] for link in preparation.LINKS}
		| {link[0] for link in preparation.AUDIT_LINKS}
	)
	before = {dt: preparation._fingerprint(dt) for dt in sorted(tracked)}
	frappe.db.savepoint("retirement_smoke")
	try:
		with (
			patch.dict(frappe.conf, {"maintenance_mode": 1, "customer_demand_cutover": 1}),
			patch("frappe.publish_realtime"),
		):
			result = retirement.promote(
				dry_run=False,
				backup_confirmed=True,
				workers_paused=True,
				expected_checksum=report["projection_checksum"],
			)
			if retire_types:
				retirement.retire(
					dry_run=False,
					backup_confirmed=True,
					workers_paused=True,
					expected_checksum=report["projection_checksum"],
				)
			verified = retirement.verify()
			assert verified["verified"]
			if retire_types:
				assert verified["retired"]
				from vuna_transport.services.demand.sales_transport_demand_service import native_customer_demand_enabled

				with patch.dict(frappe.conf, {"customer_demand_cutover": 0}):
					assert native_customer_demand_enabled()
				if verify_execution:
					from vuna_transport.tests.sales_transport_demand_smoke import run as native_smoke

					assert native_smoke(verify_execution=True, verify_planning=True)["rolled_back"]
			assert not frappe.db.exists("Transport Demand", {"source_document_type": "Delivery Order"})
			for _source, targets in report["mapping"].items():
				assert all(
					frappe.db.get_value("Transport Demand", name, "source_document_type") == "Sales Order"
					for name in targets
				)
			assert not any(
				frappe.db.exists(dt, {old: ["is", "set"]}) for dt, old, _new, _child in preparation.LINKS
			)
			result = {
				"sources": result["source_count"],
				"targets": result["target_count"],
				"uom_recovered_rows": result["uom_recovered_rows"],
				"archived_targets": len(result["archived_targets"]),
				"closed_obsolete_loads": [c["load"]["name"] for c in result["planning_to_close"]],
				"verified": True,
				"source_and_ledgers_unchanged": result["ledger_and_source_unchanged"],
				"metadata_retirement_tested": bool(retire_types),
				"native_execution_verified": bool(retire_types and verify_execution),
			}
	finally:
		frappe.db.rollback(save_point="retirement_smoke")
		frappe.db.after_commit.reset()
		frappe.clear_cache()
	assert before == {dt: preparation._fingerprint(dt) for dt in sorted(tracked)}, (
		"Rehearsal rollback did not restore protected data"
	)
	result["rolled_back"] = True
	return result
