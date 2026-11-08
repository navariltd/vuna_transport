"""Local rollback-only migration rehearsal against historical site records."""

from unittest.mock import patch

import frappe

from vuna_transport.services.demand import delivery_order_migration as migration


def run():
	"""Never commit prepared documents; report corrupt source records separately."""
	preflight = migration.prepare()
	if "mapping" not in preflight:
		raise RuntimeError(preflight)
	names = list(preflight["mapping"])
	if not names:
		raise RuntimeError("No valid historical Delivery Orders available for rehearsal")
	tracked = {dt for dt, *_rest in migration.LINKS} | {
		"Transport Demand",
		"Transport Demand Item",
		"Delivery Order",
		"Delivery Order Item",
		"Sales Order",
		"Sales Order Item",
		*migration.LEDGERS,
	}
	before = {dt: migration._fingerprint(dt) for dt in sorted(tracked)}
	frappe.db.savepoint("migration_rehearsal")
	try:
		# Local process only; never change the site's persisted maintenance setting.
		with patch.dict(frappe.conf, {"maintenance_mode": 1}):
			first = migration.prepare(
				dry_run=False, backup_confirmed=True, workers_paused=True, source_names=names
			)
			migration.verify(source_names=names)
			recovered_drafts_saved = 0
			historical_stock_guards = 0
			for target in first.get("uom_recoveries", {}):
				demand = frappe.get_doc("Transport Demand", target)
				if frappe.db.get_value("Delivery Order", demand.source_document, "docstatus") != 1:
					continue
				frappe.db.savepoint("recovered_draft_save")
				# Fixture-only: also exercise the reusable-wrapper draft state. Never
				# downgrade an operational target; this variation is rolled back below.
				if demand.docstatus == 1:
					frappe.db.set_value("Transport Demand", target, "docstatus", 0, update_modified=False)
					frappe.db.set_value(
						"Transport Demand Item", {"parent": target}, "docstatus", 0, update_modified=False
					)
					demand.reload()
				old = [
					(r.name, r.transaction_qty, r.transaction_uom, r.conversion_factor, r.stock_qty)
					for r in demand.items
				]
				if any(
					not frappe.get_cached_value("Item", r.item_code, "is_stock_item")
					or frappe.get_cached_value("Item", r.item_code, "disabled")
					for r in demand.items
				):
					try:
						demand.save(ignore_permissions=True)
					except frappe.ValidationError as error:
						assert "active stock items" in str(error)
					else:
						raise AssertionError("UOM recovery bypassed normal stock-item validation")
					historical_stock_guards += 1
					frappe.db.rollback(save_point="recovered_draft_save")
					continue
				original_snapshot = demand.legacy_delivery_order_snapshot
				demand.legacy_delivery_order_snapshot = "{}"  # Never trust client recovery metadata.
				demand.save(ignore_permissions=True)
				demand.reload()
				assert demand.legacy_delivery_order_snapshot == original_snapshot
				assert old == [
					(r.name, r.transaction_qty, r.transaction_uom, r.conversion_factor, r.stock_qty)
					for r in demand.items
				]
				recovered_drafts_saved += 1
				frappe.db.rollback(save_point="recovered_draft_save")
			# Normal save changes target timestamps. Re-test idempotence on a fresh
			# baseline while still preserving all original business evidence.
			fingerprints = {dt: migration._fingerprint(dt) for dt in sorted(tracked)}
			second = migration.prepare(
				dry_run=False, backup_confirmed=True, workers_paused=True, source_names=names
			)
			after_retry = {dt: migration._fingerprint(dt) for dt in sorted(tracked)}
			if first["mapping"] != second["mapping"] or second["create"] or second["new_links"]:
				raise RuntimeError("Retry changed the migration mapping or duplicated links")
			if fingerprints != after_retry:
				raise RuntimeError("Retry was not idempotent")
			# Exercise a normal form save too: the source rebuild must retain child
			# identities used by load/task links and the new planning field values.
			for target in first["mapping"].values():
				demand = frappe.get_doc("Transport Demand", target)
				if (
					demand.docstatus != 0
					or frappe.db.get_value("Delivery Order", demand.source_document, "docstatus") != 1
				):
					continue
				if frappe.db.get_value("Delivery Order", demand.source_document, "status") in (
					"Closed",
					"Cancelled",
					"Stopped",
				):
					continue
				old_names = {row.source_item: row.name for row in demand.items}
				cached = {row.name: (row.reserved_stock_qty, row.allocated_stock_qty) for row in demand.items}
				demand.save(ignore_permissions=True)
				demand.reload()
				if {row.source_item: row.name for row in demand.items} != old_names:
					raise RuntimeError("Normal save changed source child identities")
				if {
					row.name: (row.reserved_stock_qty, row.allocated_stock_qty) for row in demand.items
				} != cached:
					raise RuntimeError("Normal save lost allocation caches")
				if not demand.sales_order or any(not row.sales_order_item for row in demand.items):
					raise RuntimeError("Normal save lost customer planning/source fields")
				break
			else:
				raise RuntimeError("No eligible customer draft to exercise normal save")
		result = {
			"rehearsed": len(names),
			"create": first["create"],
			"reuse": first["reuse"],
			"uom_recovered_rows": first.get("uom_recovered_rows", 0),
			"recovered_draft_saves": recovered_drafts_saved,
			"historical_stock_guards_preserved": historical_stock_guards,
			"links": first["new_links"],
			"skipped_source_blockers": preflight["blockers"],
			"ledger_and_source_unchanged": first["verified"],
			"idempotent": True,
			"normal_save_preserves_row_ids_and_allocations": True,
			"ready_for_retirement": False,
		}
	finally:
		frappe.db.rollback(save_point="migration_rehearsal")
		frappe.db.after_commit.reset()
	after = {dt: migration._fingerprint(dt) for dt in sorted(tracked)}
	if before != after:
		raise RuntimeError("Rollback verification failed")
	result["all_rehearsal_data_rolled_back"] = True
	return result
