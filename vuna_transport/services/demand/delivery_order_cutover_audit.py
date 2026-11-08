"""Read-only operator review for historical customer-demand cutover.

Preparation copies are not native Sales Order-backed demand. This audit compares
the original evidence with current source rows without repairing, re-keying,
submitting, cancelling or deleting anything. Not an RPC or migration patch.
"""

import math
from collections import Counter, defaultdict

import frappe

from vuna_transport.services.demand import delivery_order_migration as preparation
from vuna_transport.services.demand.sales_transport_demand_service import (
	INACTIVE_SALES_STATUSES,
	sales_source_key,
)


def _quantity_valid(row, quantity, uom):
	try:
		qty, factor, stock = (
			float(row.get(key) or 0) for key in (quantity, "conversion_factor", "stock_qty")
		)
	except (TypeError, ValueError):
		return False
	return bool(
		row.get(uom)
		and row.get("stock_uom")
		and all(math.isfinite(n) for n in (qty, factor, stock))
		and qty >= 0
		and factor > 0
		and stock >= 0
		and preparation._same_number(qty * factor, stock)
	)


def inspect_source(snapshot, sales_order, group_keys):
	"""Pure review projection. Recovery suggestions are evidence, never authority."""
	document = snapshot["document"]
	rows = snapshot["items"]
	result = {
		"delivery_order": document["name"],
		"docstatus": document["docstatus"],
		"status": document.get("status"),
		"sales_order": document.get("sales_order"),
		"source_checksum": preparation.checksum(snapshot),
		"issues": [],
		"items": [],
		"native_source_key": None,
	}
	issues = result["issues"]
	if not sales_order:
		issues.append({"code": "missing_sales_order"})
		return result
	result["sales_order_status"] = sales_order.get("status")
	if sales_order.get("company") != document.get("company") or sales_order.get("customer") != document.get(
		"customer"
	):
		issues.append({"code": "source_party_mismatch"})
	if sales_order.get("docstatus") != 1 or sales_order.get("status") in INACTIVE_SALES_STATUSES:
		issues.append(
			{
				"code": "inactive_source",
				"decision": "Preserve historical evidence; do not regenerate from current outstanding quantity.",
			}
		)
	originals = {row["name"]: row for row in sales_order.get("items", [])}
	groups = defaultdict(list)
	seen = set()
	for row in rows:
		item = {
			"delivery_order_item": row["name"],
			"sales_order_item": row.get("sales_order_item"),
			"item_code": row.get("item_code"),
			"historical_quantity": {
				key: row.get(key)
				for key in ("transport_qty", "uom", "conversion_factor", "stock_qty", "stock_uom")
			},
		}
		result["items"].append(item)
		origin = originals.get(row.get("sales_order_item"))
		if not origin or origin.get("item_code") != row.get("item_code"):
			issues.append({"code": "source_item_mismatch", "row": row["name"]})
			continue
		if origin["name"] in seen:
			issues.append({"code": "duplicate_source_item", "row": row["name"]})
		seen.add(origin["name"])
		item["source_quantity"] = {
			key: origin.get(key) for key in ("qty", "uom", "conversion_factor", "stock_qty", "stock_uom")
		}
		if not _quantity_valid(origin, "qty", "uom"):
			issues.append({"code": "invalid_source_quantity", "row": row["name"]})
		if not _quantity_valid(row, "transport_qty", "uom"):
			issues.append({"code": "invalid_historical_quantity", "row": row["name"]})
			# Only propose the exact original source UOM when old values are absent,
			# never when an existing conversion contradicts it. No record is edited.
			if (
				_quantity_valid(origin, "qty", "uom")
				and not row.get("uom")
				and not row.get("conversion_factor")
				and not row.get("stock_qty")
				and row.get("stock_uom") in (None, "", origin.get("stock_uom"))
			):
				qty = float(row.get("transport_qty") or 0)
				if math.isfinite(qty) and 0 <= qty <= float(origin["qty"]):
					item["review_proposal"] = {
						"uom": origin["uom"],
						"conversion_factor": origin["conversion_factor"],
						"stock_uom": origin["stock_uom"],
						"stock_qty": qty * float(origin["conversion_factor"]),
						"approval_required": True,
					}
		elif (
			row.get("uom") != origin.get("uom")
			or row.get("stock_uom") != origin.get("stock_uom")
			or not preparation._same_number(row.get("conversion_factor"), origin.get("conversion_factor"))
		):
			issues.append({"code": "source_uom_changed", "row": row["name"]})
		if origin["name"] in group_keys:
			groups[group_keys[origin["name"]]].append(origin["name"])
	result["source_groups"] = [
		{"group_key": list(key), "sales_order_items": names}
		for key, names in sorted(groups.items(), key=lambda pair: str(pair[0]))
	]
	if len(groups) != 1:
		issues.append({"code": "source_group_split_required", "groups": len(groups)})
	elif len(seen) == len(rows):
		result["native_source_key"] = sales_source_key(sales_order["name"], next(iter(groups)), list(seen))
	return result


def audit(source_names: list[str] | None = None) -> dict:
	"""Administrator CLI review. Verify that even this audit changes no records."""
	preparation._authorize(True, False, False)
	preflight = preparation.prepare(source_names=source_names)
	if "mapping" not in preflight:
		return {"mode": "read_only_cutover_audit", "preparation": preflight, "cutover_ready": False}
	names = sorted(
		set(source_names if source_names is not None else frappe.get_all("Delivery Order", pluck="name"))
	)
	tracked = {
		"Delivery Order",
		"Delivery Order Item",
		"Transport Demand",
		"Transport Demand Item",
		"Sales Order",
		"Sales Order Item",
		"Transport Load",
		*preparation.LEDGERS,
		*(link[0] for link in preparation.LINKS),
	}
	before = {dt: preparation._fingerprint(dt) for dt in sorted(tracked)}
	from vuna_transport.services.demand import delivery_order_service as sales

	sources, cases = {}, []
	key_owners, row_owners = defaultdict(list), defaultdict(list)
	for name in names:
		snapshot = preparation._source(name)
		order = snapshot["document"]
		if not order:
			continue  # prepare already reports a missing explicitly selected source.
		source_name = order.get("sales_order")
		if source_name not in sources:
			sources[source_name] = (
				frappe.get_doc("Sales Order", source_name)
				if source_name and frappe.db.exists("Sales Order", source_name)
				else None
			)
		original = sources[source_name]
		group_keys, grouping_error = {}, None
		if original:
			selected = {r.get("sales_order_item") for r in snapshot["items"]}
			try:
				group_keys = {
					row.name: sales._get_group_key(original, row)
					for row in original.items
					if row.name in selected
				}
			except (frappe.ValidationError, frappe.DoesNotExistError) as error:
				grouping_error = str(error)
		case = inspect_source(snapshot, original.as_dict() if original else None, group_keys)
		if grouping_error:
			case["issues"].append({"code": "current_grouping_invalid", "detail": grouping_error})
		case["preparation_passed"] = name in preflight["mapping"]
		case["target"] = preflight["mapping"].get(name)
		case["uom_recoveries"] = preflight.get("uom_recoveries", {}).get(case["target"], [])
		recovered_rows = {row["delivery_order_item"] for row in case["uom_recoveries"]}
		for issue in case["issues"]:
			if issue["code"] == "invalid_historical_quantity" and issue.get("row") in recovered_rows:
				issue["resolved_in_preparation"] = True
		case["references"] = {
			dt: frappe.get_all(dt, filters={old: name}, pluck="name")
			for dt, old, new, child in preparation.LINKS
			if not child
		}
		case["load_references"] = []
		for row in frappe.get_all(
			"Transport Load Item",
			filters={"delivery_order": name},
			fields=["name", "parent", "allocated_quantity", "uom", "conversion_factor"],
		):
			load = frappe.db.get_value(
				"Transport Load",
				row.parent,
				["name", "docstatus", "status", "delivery_trip", "active_vehicle_allocation"],
				as_dict=True,
			)
			case["load_references"].append(
				{
					"load_item": row.name,
					"allocated_quantity": row.allocated_quantity,
					"uom": row.uom,
					"conversion_factor": row.conversion_factor,
					"load": dict(load) if load else None,
				}
			)
		if case["native_source_key"]:
			key_owners[case["native_source_key"]].append(case)
		if order.get("docstatus") != 2:
			for row in snapshot["items"]:
				if row.get("sales_order_item"):
					row_owners[(source_name, row["sales_order_item"])].append(case)
		cases.append(case)
	for key, owners in key_owners.items():
		existing = frappe.db.get_value("Transport Demand", {"source_key": key}, "name")
		for case in owners:
			if len(owners) > 1 or (existing and existing != case["target"]):
				case["issues"].append(
					{
						"code": "native_key_collision",
						"existing_target": existing,
						"orders": [r["delivery_order"] for r in owners],
					}
				)
	for (_source_name, row_name), owners in row_owners.items():
		if len({case["delivery_order"] for case in owners}) > 1:
			for case in owners:
				case["issues"].append(
					{
						"code": "shared_source_row",
						"sales_order_item": row_name,
						"orders": sorted({r["delivery_order"] for r in owners}),
					}
				)
	for case in cases:
		case["review_required"] = bool(
			any(not issue.get("resolved_in_preparation") for issue in case["issues"])
			or not case["preparation_passed"]
		)
	after = {dt: preparation._fingerprint(dt) for dt in sorted(tracked)}
	if before != after:
		raise preparation.MigrationConflict(
			"Protected data changed during the audit; retry on a quiet/restored snapshot. This audit performs no writes."
		)
	return {
		"mode": "read_only_cutover_audit",
		"site": frappe.local.site,
		"source_count": len(names),
		"preparation_candidates": len(preflight["mapping"]),
		"uom_recovered_rows": preflight.get("uom_recovered_rows", 0),
		"preparation_blockers": preflight["blockers"],
		"review_required": sum(c["review_required"] for c in cases),
		"issue_counts": dict(Counter(issue["code"] for c in cases for issue in c["issues"])),
		"outstanding_issue_counts": dict(
			Counter(
				issue["code"]
				for c in cases
				for issue in c["issues"]
				if not issue.get("resolved_in_preparation")
			)
		),
		"cases": cases,
		"review_checksum": preparation.checksum(cases),
		"unchanged_tables": before,
		"cutover_ready": False,
		"site_setting_changed": False,
		"next_step": "Preparation includes verified Sales Order UOM recovery. Review remaining source-group/history cases before historical promotion.",
	}


def summary() -> dict:
	"""Bounded console review; use audit() for the full fingerprinted inventory."""
	report = audit()
	return {key: value for key, value in report.items() if key not in ("cases", "unchanged_tables")} | {
		"review_cases": [case for case in report.get("cases", []) if case["review_required"]]
	}
