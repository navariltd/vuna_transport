"""Guarded historical promotion and final Delivery Order retirement.

No ERPNext stock/accounting hooks are replayed. The original source tables are
retained as forensic backup; complete source/audit snapshots live on the demands.
"""

import copy
import json
from collections import defaultdict

import frappe
from frappe.utils import flt

from vuna_transport.services.demand import delivery_order_migration as preparation
from vuna_transport.services.demand.sales_transport_demand_service import (
	INACTIVE_SALES_STATUSES,
	sales_source_key,
)

MANIFEST_FIELD = "delivery_order_retirement_manifest"
RETIRED_TYPES = ("Delivery Order", "Delivery Order Item")


def project_groups(plan, sales_order, group_keys, archived_items=()):
	"""Pure row-preserving one-to-many projection; never merge source identities."""
	root = plan["demand"]
	groups = defaultdict(list)
	originals = {row["name"]: row for row in sales_order["items"]}
	for row in plan["items"]:
		original = originals.get(row.get("sales_order_item"))
		preparation._conflict(
			not original or original.get("item_code") != row["item_code"],
			f"{root['name']}: exact Sales Order row required for promotion",
		)
		preparation._conflict(
			row["transaction_uom"] != original["uom"]
			or row["stock_uom"] != original["stock_uom"]
			or not preparation._same_number(row["conversion_factor"], original["conversion_factor"]),
			f"{root['name']}/{row['name']}: current source UOM conflicts with history",
		)
		key = group_keys[original["name"]]
		groups[(key, row["item_code"] in archived_items)].append(row)
	result = []
	for index, ((key, non_stock), rows) in enumerate(
		sorted(groups.items(), key=lambda entry: str(entry[0])), 1
	):
		archived = (
			non_stock
			or sales_order.get("docstatus") != 1
			or sales_order.get("status") in INACTIVE_SALES_STATUSES
			or plan["source"]["document"]["docstatus"] == 2
		)
		name = root["name"] if index == 1 else f"{root['name']}-G{index:02}"
		target = copy.deepcopy(root)
		target.update(
			name=name,
			source_document_type="Sales Order",
			source_document=sales_order["name"],
			sales_order=sales_order["name"],
			migration_archived=int(archived),
			docstatus=plan["source"]["document"]["docstatus"],
			source_key=sales_source_key(sales_order["name"], key, [r["sales_order_item"] for r in rows]),
		)
		if archived:
			target["source_key"] = preparation.checksum(
				["historical", root["legacy_delivery_order"], target["source_key"]]
			)
			target["status"] = "Cancelled" if target["docstatus"] == 2 else "Closed"
		else:
			target.update(required_date=key[0], destination_address=key[1], delivery_group=key[-1])
		items = []
		for idx, row in enumerate(rows, 1):
			item = copy.deepcopy(row)
			item.update(
				parent=name, source_item=row["sales_order_item"], idx=idx, docstatus=target["docstatus"]
			)
			items.append(item)
		for dimension in ("weight", "volume", "pallets", "cases", "crates"):
			target["total_" + dimension] = sum(flt(r.get(dimension)) for r in items)
		for unit in ("weight_uom", "volume_uom"):
			target[unit] = next((r.get(unit) for r in items if r.get(unit)), None)
		result.append({"demand": target, "items": items})
	return result


def _projections(plans):
	from vuna_transport.services.demand import delivery_order_service as sales

	targets, errors = [], []
	for plan in plans:
		try:
			source = frappe.get_doc("Sales Order", plan["demand"]["sales_order"])
			# Legacy sync sometimes reduced the DO's remaining quantity to zero
			# after dispatch. Preserve the quantity actually represented by its
			# non-cancelled load rows instead of importing an empty demand.
			commitments = dict(
				frappe.db.sql(
					"""select i.delivery_order_item,sum(i.allocated_quantity)
			from `tabTransport Load Item` i join `tabTransport Load` l on l.name=i.parent
			where i.delivery_order=%s and l.docstatus in (0,1) group by i.delivery_order_item""",
					plan["source"]["document"]["name"],
				)
			)
			plan = copy.deepcopy(plan)
			for row in plan["items"]:
				row["transaction_qty"] = max(
					flt(row["transaction_qty"]), flt(commitments.get(row["source_item"]))
				)
				row["stock_qty"] = row["transaction_qty"] * row["conversion_factor"]
				original = next((r for r in source.items if r.name == row["sales_order_item"]), None)
				preparation._conflict(not original, f"{row['name']}: original Sales Order row is missing")
				preparation._conflict(
					row["transaction_qty"] > flt(original.qty) + 1e-6,
					f"{row['name']}: historical commitments exceed the original sales quantity",
				)
				if frappe.get_cached_value("Item", row["item_code"], "is_stock_item"):
					measured = sales._get_delivery_order_item_values(
						original,
						row["transaction_qty"],
						row.get("transport_compatibility_group"),
						row.get("delivery_group"),
					)
					for key in ("weight", "weight_uom", "volume", "volume_uom", "pallets", "cases", "crates"):
						row[key] = measured.get(key)
			selected = {r["sales_order_item"] for r in plan["items"]}
			keys = {
				row.name: sales._get_group_key(source, row) for row in source.items if row.name in selected
			}
			archived = {
				r["item_code"]
				for r in plan["items"]
				if not frappe.get_cached_value("Item", r["item_code"], "is_stock_item")
				or frappe.get_cached_value("Item", r["item_code"], "disabled")
			}
			for target in project_groups(plan, source.as_dict(), keys, archived):
				key_owner = frappe.db.get_value(
					"Transport Demand", {"source_key": target["demand"]["source_key"]}, "name"
				)
				preparation._conflict(
					key_owner and key_owner != target["demand"]["name"],
					f"Native source key collision: {key_owner}",
				)
				if target["demand"]["name"] != plan["demand"]["name"]:
					preparation._conflict(
						frappe.db.exists("Transport Demand", target["demand"]["name"]),
						f"Split target name already exists: {target['demand']['name']}",
					)
				target["legacy_source"] = plan["source"]["document"]["name"]
				targets.append(target)
		except (
			preparation.MigrationConflict,
			frappe.DoesNotExistError,
			frappe.ValidationError,
			KeyError,
		) as error:
			errors.append(str(error))
	keys = [target["demand"]["source_key"] for target in targets]
	if len(keys) != len(set(keys)):
		errors.append("Multiple historical demands project to the same native sales source key")
	return targets, errors


def _obsolete_loads(targets):
	"""Only close wholly historical planning with no active stock/proof/trip."""
	archived_sources = {p["legacy_source"] for p in targets if p["demand"]["migration_archived"]}
	live_sources = {p["legacy_source"] for p in targets if not p["demand"]["migration_archived"]}
	closures = []
	if not archived_sources:
		return closures
	loads = frappe.db.sql_list(
		"""select distinct i.parent from `tabTransport Load Item` i
	join `tabTransport Load` l on l.name=i.parent where i.delivery_order in %(names)s and l.docstatus in (0,1)""",
		{"names": tuple(archived_sources)},
	)
	for name in loads:
		load = frappe.get_doc("Transport Load", name)
		if any(row.delivery_order in live_sources or not row.delivery_order for row in load.delivery_orders):
			raise preparation.MigrationConflict(
				f"{name}: historical-only and active demand share a pending load; review before retirement"
			)
		preparation._conflict(
			bool(load.delivery_trip)
			or frappe.db.exists("Proof of Delivery", {"delivery_load": name, "docstatus": ["!=", 2]})
			or frappe.db.exists(
				"Transport Trip Task", {"transport_load": name, "status": ["!=", "Cancelled"]}
			)
			or frappe.db.exists("Delivery Note", {"vuna_delivery_load": name, "docstatus": ["!=", 2]})
			or frappe.db.exists("Stock Entry", {"vuna_transport_load": name, "docstatus": ["!=", 2]}),
			f"{name}: historical source still has stock/trip execution; cannot close planning automatically",
		)
		allocation = (
			frappe.get_doc("Vehicle Allocation", load.active_vehicle_allocation)
			if load.active_vehicle_allocation
			else None
		)
		if allocation and allocation.docstatus != 2:
			from vuna_transport.services.fleet.vehicle_allocation_service import get_allocation_load_names

			preparation._conflict(
				any(other != name for other in get_allocation_load_names(allocation)),
				f"{allocation.name}: shared allocation requires explicit historical-load resolution",
			)
			preparation._conflict(
				frappe.db.exists(
					"Delivery Trip", {"vuna_vehicle_allocation": allocation.name, "docstatus": ["!=", 2]}
				)
				or frappe.db.exists(
					"Stock Entry", {"vuna_vehicle_allocation": allocation.name, "docstatus": ["!=", 2]}
				),
				f"{allocation.name}: execution exists; cannot close historical planning",
			)
		closures.append({"load": load.as_dict(), "allocation": allocation.as_dict() if allocation else None})
	return closures


def _reconcile_headers(targets):
	"""A split source may no longer fit in a single parent-level Link."""
	for dt, child, parent_field, item_field in (
		("Proof of Delivery", "Proof of Delivery Item", "transport_demand", "transport_demand_item"),
		("Return Request", "Return Request Item", "transport_demand", "transport_demand_item"),
	):
		for name in frappe.get_all(
			dt, filters={parent_field: ["in", [p["demand"]["name"] for p in targets]]}, pluck="name"
		):
			items = frappe.get_all(
				child, filters={"parent": name, item_field: ["is", "set"]}, pluck=item_field
			)
			parents = (
				set(frappe.get_all("Transport Demand Item", filters={"name": ["in", items]}, pluck="parent"))
				if items
				else set()
			)
			if parents:
				frappe.db.set_value(
					dt,
					name,
					parent_field,
					next(iter(parents)) if len(parents) == 1 else None,
					update_modified=False,
				)
	for note in frappe.get_all(
		"Delivery Note",
		filters={"vuna_transport_demand": ["in", [p["demand"]["name"] for p in targets]]},
		pluck="name",
	):
		parents = set(
			frappe.db.sql_list(
				"""select distinct l.transport_demand from `tabDelivery Note Item` n
		join `tabTransport Load Item` l on l.name=n.vuna_delivery_load_order where n.parent=%s and l.transport_demand is not null""",
				note,
			)
		)
		if parents:
			value = next(iter(parents)) if len(parents) == 1 else None
			frappe.db.set_value("Delivery Note", note, "vuna_transport_demand", value, update_modified=False)
			frappe.db.set_value(
				"Delivery Stop",
				{"delivery_note": note},
				"vuna_transport_demand",
				value,
				update_modified=False,
			)


def _port_references(mapping, item_mapping):
	"""Move standard audit links and external site-specific Link references."""
	orphans = []
	# Schema preparation may already have changed this field's Link target.
	# Its stored legacy value still needs the same exact target mapping.
	for row in frappe.get_all(
		"Return Request",
		filters={"replacement_delivery_reference": ["in", list(mapping)]},
		fields=["name", "replacement_delivery_reference"],
	):
		frappe.db.set_value(
			"Return Request",
			row.name,
			"replacement_delivery_reference",
			mapping[row.replacement_delivery_reference][0],
			update_modified=False,
		)
	for dt, type_field, name_field in preparation.AUDIT_LINKS:
		for row in frappe.get_all(dt, filters={type_field: "Delivery Order"}, fields=["*"]):
			if row[name_field] not in mapping:
				orphans.append({"doctype": dt, "record": dict(row)})
				if dt == "DocShare":
					# An orphan grant cannot be reassigned to an unrelated document.
					frappe.db.delete(dt, {"name": row.name})
				else:
					frappe.db.set_value(
						dt, row.name, {type_field: None, name_field: None}, update_modified=False
					)
				continue
			frappe.db.set_value(
				dt,
				row.name,
				{type_field: "Transport Demand", name_field: mapping[row[name_field]][0]},
				update_modified=False,
			)
			if dt == "DocShare":
				for target in mapping[row[name_field]][1:]:
					grant = dict(row)
					grant.update(
						doctype="DocShare",
						name=frappe.generate_hash(length=10),
						share_doctype="Transport Demand",
						share_name=target,
					)
					frappe.get_doc(grant).db_insert()
	known = {(dt, old) for dt, old, new, child in preparation.LINKS}
	for meta_type, owner_field in (("DocField", "parent"), ("Custom Field", "dt")):
		for field in frappe.get_all(
			meta_type,
			filters={"options": ["in", list(RETIRED_TYPES)], "fieldtype": "Link"},
			fields=["name", owner_field, "fieldname", "options"],
		):
			dt = field[owner_field]
			if dt in RETIRED_TYPES:
				continue
			if (dt, field.fieldname) in known:
				frappe.db.set_value(
					meta_type,
					field.name,
					{"fieldtype": "Data", "options": None, "hidden": 1, "read_only": 1},
					update_modified=False,
				)
				continue
			values = mapping if field.options == "Delivery Order" else item_mapping
			for row in frappe.get_all(
				dt, filters={field.fieldname: ["is", "set"]}, fields=["name", field.fieldname]
			):
				preparation._conflict(
					row[field.fieldname] not in values,
					f"Unknown source reference: {dt}.{field.fieldname} {row.name}",
				)
				target = (
					values[row[field.fieldname]][0]
					if field.options == "Delivery Order"
					else values[row[field.fieldname]]
				)
				frappe.db.set_value(dt, row.name, field.fieldname, target, update_modified=False)
			frappe.db.set_value(
				meta_type,
				field.name,
				"options",
				"Transport Demand" if field.options == "Delivery Order" else "Transport Demand Item",
				update_modified=False,
			)
	frappe.db.delete("DocType Link", {"link_doctype": ["in", list(RETIRED_TYPES)]})
	return orphans


def _unsupported_references():
	"""Never silently redirect custom automations or unknown financial references."""
	errors = []
	known = {(dt, name_field) for dt, _type, name_field in preparation.AUDIT_LINKS}
	known.add(("Transport Demand", "source_document"))
	for metadata, owner in (("DocField", "parent"), ("Custom Field", "dt")):
		for field in frappe.get_all(
			metadata, filters={"fieldtype": "Dynamic Link"}, fields=[owner, "fieldname", "options"]
		):
			dt = field[owner]
			if dt in RETIRED_TYPES or (dt, field.fieldname) in known:
				continue
			meta = frappe.get_meta(dt)
			if meta.issingle or meta.is_virtual or not meta.has_field(field.options):
				continue
			if frappe.db.exists(dt, {field.options: ["in", list(RETIRED_TYPES)]}):
				errors.append(f"Review external Dynamic Link before retirement: {dt}.{field.fieldname}")
	for dt, field in (
		("Workflow", "document_type"),
		("Notification", "document_type"),
		("Client Script", "dt"),
		("Server Script", "reference_doctype"),
		("Print Format", "doc_type"),
	):
		if frappe.db.exists("DocType", dt) and frappe.db.exists(dt, {field: ["in", list(RETIRED_TYPES)]}):
			errors.append(f"Review site-specific {dt} configuration before retirement")
	return errors


def proof_unit_recovery(proof, note):
	"""Recover absent units only where the exact voucher proves the old quantity basis."""
	preparation._conflict(
		not note or proof["item_code"] != note["item_code"],
		"Historical POD item must match its exact Delivery Note row",
	)
	ordered = flt(proof["ordered_qty"])
	preparation._conflict(ordered <= 0, "Historical POD has no positive ordered quantity")
	if preparation._same_number(ordered, note["stock_qty"]):
		uom, factor = note["stock_uom"], 1
	elif preparation._same_number(ordered, note["qty"]):
		uom, factor = note["uom"], flt(note["conversion_factor"])
	else:
		raise preparation.MigrationConflict(
			f"{proof['name']}: historical proof quantity basis requires manual review"
		)
	preparation._conflict(
		not uom
		or not note["stock_uom"]
		or factor <= 0
		or proof.get("uom") not in (None, "", uom)
		or proof.get("stock_uom") not in (None, "", note["stock_uom"]),
		"Historical POD UOM contradicts its voucher evidence",
	)
	accepted, rejected = flt(proof["delivered_qty"]), flt(proof["rejected_qty"])
	preparation._conflict(
		accepted < 0 or rejected < 0 or accepted + rejected > ordered + 1e-6,
		"Historical POD outcome quantities require review",
	)
	return {
		"uom": uom,
		"stock_uom": note["stock_uom"],
		"conversion_factor": factor,
		"stock_qty": accepted * factor,
		"rejected_stock_qty": rejected * factor,
	}


def _recover_proof_units():
	recoveries = []
	for row in frappe.get_all(
		"Proof of Delivery Item",
		filters={"transport_demand_item": ["is", "set"], "conversion_factor": ["<=", 0]},
		fields=["*"],
	):
		note = (
			frappe.db.get_value(
				"Delivery Note Item",
				row.delivery_note_item,
				["item_code", "qty", "uom", "conversion_factor", "stock_qty", "stock_uom"],
				as_dict=True,
			)
			if row.delivery_note_item
			else None
		)
		values = proof_unit_recovery(row, note)
		recoveries.append(
			{
				"before": dict(row),
				"delivery_note_item": row.delivery_note_item,
				"voucher_evidence": dict(note),
				"resolved": values,
			}
		)
		frappe.db.set_value("Proof of Delivery Item", row.name, values, update_modified=False)
	return recoveries


def recover_historical_proof_units(backup_confirmed: bool = False, workers_paused: bool = False) -> dict:
	"""Idempotent companion for sites already promoted before proof-unit recovery."""
	preparation._authorize(False, backup_confirmed, workers_paused)
	verify()
	frappe.db.savepoint("historical_proof_units")
	try:
		baseline = {dt: preparation._fingerprint(dt) for dt in preparation.LEDGERS}
		recoveries = _recover_proof_units()
		manifest = json.loads(frappe.db.get_single_value("Transport Settings", MANIFEST_FIELD))
		manifest.setdefault("proof_uom_recoveries", []).extend(recoveries)
		frappe.db.set_single_value("Transport Settings", MANIFEST_FIELD, preparation._json(manifest))
		from vuna_transport.services.planning.demand_lifecycle_service import sync_demand_allocations

		sync_demand_allocations([name for names in manifest["mapping"].values() for name in names])
		preparation._conflict(
			any(preparation._fingerprint(dt) != before for dt, before in baseline.items()),
			"Ledger changed during proof-unit recovery",
		)
		return {"recovered_proof_rows": len(recoveries), "ledger_unchanged": True}
	except Exception:
		frappe.db.rollback(save_point="historical_proof_units")
		raise


def inspect() -> dict:
	"""Read-only full-site retirement plan, distinct from preparation readiness."""
	preparation._authorize(True, False, False)
	manifest = frappe.db.get_single_value("Transport Settings", MANIFEST_FIELD)
	if manifest:
		stored = json.loads(manifest)
		if stored.get("state") in ("promoted", "retired"):
			return {**stored, "mode": "already_promoted", "verification": verify()}
	if not frappe.db.exists("DocType", "Delivery Order"):
		preparation._conflict(
			frappe.db.table_exists("Delivery Order")
			and frappe.db.sql("select name from `tabDelivery Order` limit 1"),
			"Delivery Order history exists without metadata or a retirement manifest; restore the backup metadata before migration",
		)
		return {"retired": True, "mode": "inspect", "source_count": 0}
	preflight = preparation.prepare()
	errors = list(preflight["blockers"])
	errors.extend(_unsupported_references())
	plans = preparation._plans(sorted(preflight.get("mapping", {})), errors)
	targets, projection_errors = _projections(plans)
	errors.extend(projection_errors)
	try:
		closures = _obsolete_loads(targets)
	except preparation.MigrationConflict as error:
		errors.append(str(error))
		closures = []
	return {
		"mode": "inspect",
		"source_count": preflight["source_count"],
		"target_count": len(targets),
		"archived_targets": [p["demand"]["name"] for p in targets if p["demand"]["migration_archived"]],
		"mapping": {
			name: [p["demand"]["name"] for p in targets if p["legacy_source"] == name]
			for name in sorted(preflight.get("mapping", {}))
		},
		"uom_recovered_rows": preflight.get("uom_recovered_rows", 0),
		"blockers": errors,
		"planning_to_close": closures,
		"projection_checksum": preparation.checksum([targets, closures]),
		"ready_for_apply": not errors,
		"retired": False,
	}


def promote(
	dry_run: bool = True,
	backup_confirmed: bool = False,
	workers_paused: bool = False,
	expected_checksum: str | None = None,
) -> dict:
	"""Promote all source history; metadata deletion is a separately verified step."""
	preparation._authorize(dry_run, backup_confirmed, workers_paused)
	report = inspect()
	if dry_run or report.get("retired") or report.get("mode") == "already_promoted":
		return report
	preparation._conflict(report["blockers"], preparation._json(report))
	preparation._conflict(
		not expected_checksum or report["projection_checksum"] != expected_checksum,
		"Inspect and confirm the current projection_checksum before promotion",
	)
	names = sorted(report["mapping"])
	if not names:
		frappe.db.set_single_value(
			"Transport Settings",
			MANIFEST_FIELD,
			preparation._json(
				{
					**report,
					"state": "promoted",
					"item_mapping": {},
					"item_parents": {},
				}
			),
		)
		return {**report, "mode": "promoted"}
	preparation._lock_sources(names)
	# Re-inspect after locks; a source edit must not invalidate an approved plan.
	preparation._conflict(
		inspect()["projection_checksum"] != expected_checksum,
		"Retirement inputs changed while acquiring locks",
	)
	frappe.db.savepoint("delivery_order_promotion")
	try:
		unchanged = {
			dt: preparation._fingerprint(dt)
			for dt in (*preparation.LEDGERS, *RETIRED_TYPES, "Sales Order", "Sales Order Item")
		}
		preparation.prepare(dry_run=False, backup_confirmed=backup_confirmed, workers_paused=workers_paused)
		errors = []
		plans = preparation._plans(names, errors)
		targets, projection_errors = _projections(plans)
		preparation._conflict(errors or projection_errors, preparation._json(errors + projection_errors))
		by_item = {row["name"]: p["demand"]["name"] for p in targets for row in p["items"]}
		item_map = {row["source_item"]: row["name"] for plan in plans for row in plan["items"]}
		load_snapshots = {
			name: frappe.get_doc("Transport Load", name).as_dict()
			for name in frappe.db.sql_list(
				"""select distinct parent from `tabTransport Load Item` where delivery_order in %(names)s""",
				{"names": tuple(names)},
			)
		}
		for target in targets:
			values = target["demand"]
			base = next(p for p in plans if p["source"]["document"]["name"] == target["legacy_source"])
			snapshot = json.loads(
				frappe.db.get_value(
					"Transport Demand", base["demand"]["name"], "legacy_delivery_order_snapshot"
				)
			)
			snapshot["retirement"] = {
				"targets": report["mapping"][target["legacy_source"]],
				"projection_checksum": expected_checksum,
			}
			values["legacy_delivery_order_snapshot"] = preparation._json(snapshot)
			if frappe.db.exists("Transport Demand", values["name"]):
				frappe.db.set_value(
					"Transport Demand",
					values["name"],
					{k: v for k, v in values.items() if k not in ("name", "doctype")},
					update_modified=False,
				)
			else:
				frappe.get_doc(values).db_insert()
			for row in target["items"]:
				frappe.db.set_value(
					"Transport Demand Item",
					row["name"],
					{k: v for k, v in row.items() if k not in ("name", "doctype")},
					update_modified=False,
				)
		# Item-level identity decides parent mapping for split demands.
		for dt, old, new, child in preparation.LINKS:
			fields = ["name", old]
			if dt == "Transport Load Item":
				fields.append("delivery_order_item")
			for row in frappe.get_all(dt, filters={old: ["is", "set"]}, fields=fields):
				value = (
					item_map[row[old]]
					if child
					else (
						by_item[item_map[row.delivery_order_item]]
						if dt == "Transport Load Item"
						else report["mapping"][row[old]][0]
					)
				)
				frappe.db.set_value(dt, row.name, {new: value, old: None}, update_modified=False)
		load_names = frappe.db.sql_list(
			"""select distinct parent from `tabTransport Load Item` where transport_demand in %(names)s""",
			{"names": tuple(p["demand"]["name"] for p in targets)},
		)
		for name in load_names:
			frappe.db.set_value(
				"Transport Load", name, "movement_type", "Customer Delivery", update_modified=False
			)
		for row in frappe.get_all(
			"Transport Load Item",
			filters={"parent": ["in", load_names]},
			fields=["name", "transport_demand_item", "allocated_quantity"],
		):
			item = next(
				(
					item
					for target in targets
					for item in target["items"]
					if item["name"] == row.transport_demand_item
				),
				None,
			)
			if not item:
				continue
			ratio = (
				flt(row.allocated_quantity) / flt(item["transaction_qty"]) if item["transaction_qty"] else 0
			)
			values = {
				key: item.get(key)
				for key in (
					"item_code",
					"item_name",
					"stock_uom",
					"conversion_factor",
					"weight_uom",
					"volume_uom",
				)
			}
			values.update(
				uom=item["transaction_uom"],
				allocated_stock_qty=flt(row.allocated_quantity) * item["conversion_factor"],
				**{
					dim: flt(item.get(dim)) * ratio
					for dim in ("weight", "volume", "pallets", "cases", "crates")
				},
			)
			frappe.db.set_value("Transport Load Item", row.name, values, update_modified=False)
		for name in load_names:
			rows = frappe.get_all(
				"Transport Load Item",
				filters={"parent": name},
				fields=["weight", "volume", "pallets", "cases", "crates", "weight_uom", "volume_uom"],
			)
			values = {
				"total_" + dim: sum(flt(row.get(dim)) for row in rows)
				for dim in ("weight", "volume", "pallets", "cases", "crates")
			}
			values.update(
				{
					unit: next((row.get(unit) for row in rows if row.get(unit)), None)
					for unit in ("weight_uom", "volume_uom")
				}
			)
			frappe.db.set_value("Transport Load", name, values, update_modified=False)
		for closure in report["planning_to_close"]:
			name = closure["load"]["name"]
			frappe.db.set_value(
				"Transport Load",
				name,
				{"docstatus": 2, "status": "Cancelled", "active_vehicle_allocation": None},
				update_modified=False,
			)
			frappe.db.set_value(
				"Transport Load Item", {"parent": name}, "docstatus", 2, update_modified=False
			)
			allocation = closure["allocation"]
			if allocation and allocation["docstatus"] != 2:
				frappe.db.set_value(
					"Vehicle Allocation",
					allocation["name"],
					{"docstatus": 2, "status": "Cancelled"},
					update_modified=False,
				)
				frappe.db.set_value(
					"Vehicle Allocation Load",
					{"parent": allocation["name"]},
					"docstatus",
					2,
					update_modified=False,
				)
		_reconcile_headers(targets)
		proof_recoveries = _recover_proof_units()
		from vuna_transport.services.planning.demand_lifecycle_service import sync_demand_allocations

		sync_demand_allocations([target["demand"]["name"] for target in targets])
		orphan_audit = _port_references(report["mapping"], item_map)
		manifest = {
			"format_version": 1,
			**report,
			"state": "promoted",
			"item_mapping": item_map,
			"item_parents": by_item,
			"unchanged_tables": unchanged,
			"load_snapshots": load_snapshots,
			"orphan_audit_archive": orphan_audit,
			"proof_uom_recoveries": proof_recoveries,
		}
		frappe.db.set_single_value("Transport Settings", MANIFEST_FIELD, preparation._json(manifest))
		preparation._conflict(
			any(preparation._fingerprint(dt) != baseline for dt, baseline in unchanged.items()),
			"Source or stock/accounting changed during promotion",
		)
		return {**report, "mode": "promoted", "ledger_and_source_unchanged": True, "retired": False}
	except Exception:
		frappe.db.rollback(save_point="delivery_order_promotion")
		raise


def verify() -> dict:
	"""Verify preserved snapshots, exact native item identity and absence of old links."""
	preparation._authorize(True, False, False)
	raw = frappe.db.get_single_value("Transport Settings", MANIFEST_FIELD)
	preparation._conflict(not raw, "No verified retirement manifest exists")
	manifest = json.loads(raw)
	for source, targets in manifest["mapping"].items():
		for name in targets:
			demand = frappe.get_doc("Transport Demand", name)
			preparation._conflict(
				demand.source_document_type != "Sales Order" or demand.legacy_delivery_order != source,
				f"{name}: native source verification failed",
			)
			snapshot = json.loads(demand.legacy_delivery_order_snapshot)
			preparation._conflict(
				preparation.checksum(snapshot["source"]) != demand.legacy_delivery_order_checksum,
				f"{name}: original snapshot verification failed",
			)
		for old_item, new_item in manifest["item_mapping"].items():
			if old_item not in snapshot["item_map"]:
				continue
			values = frappe.db.get_value(
				"Transport Demand Item", new_item, ["parent", "source_item", "sales_order_item"], as_dict=True
			)
			preparation._conflict(
				not values
				or values.parent != manifest["item_parents"][new_item]
				or values.source_item != values.sales_order_item,
				f"{new_item}: item lineage verification failed",
			)
	for dt, old, _new, _child in preparation.LINKS:
		preparation._conflict(
			frappe.db.exists(dt, {old: ["is", "set"]}), f"Legacy references remain in {dt}.{old}"
		)
	preparation._conflict(
		frappe.db.exists("Transport Demand", {"source_document_type": "Delivery Order"}),
		"Legacy demand sources remain",
	)
	preparation._conflict(_unsupported_references(), "External references still require review")
	return {
		"verified": True,
		"sources": manifest["source_count"],
		"targets": manifest["target_count"],
		"retired": not any(frappe.db.exists("DocType", name) for name in RETIRED_TYPES),
	}


def install_schema(backup_confirmed: bool = False, workers_paused: bool = False) -> dict:
	"""Explicit pre-upgrade schema preparation; DDL is outside data savepoints."""
	preparation._authorize(False, backup_confirmed, workers_paused)
	for name in (
		"transport_demand_item",
		"transport_demand",
		"transport_load_item",
		"proof_of_delivery_item",
		"proof_of_delivery",
		"return_request_item",
		"return_request",
		"transport_settings",
	):
		frappe.reload_doc("vuna_transport", "doctype", name, force=True)
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	for name in ("delivery_note", "delivery_stop"):
		data = frappe.get_file_json(
			frappe.get_app_path("vuna_transport", "vuna_transport", "custom", name + ".json")
		)
		fields = [field for field in data["custom_fields"] if field["fieldname"] == "vuna_transport_demand"]
		if fields:
			create_custom_fields({data["doctype"]: fields}, update=True)
	frappe.clear_cache()
	return {"schema_ready": True, "retired": not any(frappe.db.exists("DocType", dt) for dt in RETIRED_TYPES)}


def retire(
	dry_run: bool = True,
	backup_confirmed: bool = False,
	workers_paused: bool = False,
	expected_checksum: str | None = None,
) -> dict:
	"""Promote and remove metadata atomically; retain all raw source tables."""
	preparation._authorize(dry_run, backup_confirmed, workers_paused)
	if dry_run:
		return inspect()
	frappe.db.savepoint("delivery_order_retirement")
	try:
		promote(
			dry_run=False,
			backup_confirmed=backup_confirmed,
			workers_paused=workers_paused,
			expected_checksum=expected_checksum,
		)
		verify()
		# Developer-mode export must not delete files during a rollback rehearsal.
		developer_mode, migrating = frappe.conf.get("developer_mode"), frappe.flags.in_migrate
		frappe.conf.developer_mode, frappe.flags.in_migrate = 0, True
		try:
			for name in ("Open Delivery Demand", "Unallocated Delivery Orders"):
				if frappe.db.exists("Report", name):
					frappe.delete_doc("Report", name, force=True, ignore_permissions=True)
			for name in RETIRED_TYPES:
				if frappe.db.exists("DocType", name):
					frappe.delete_doc("DocType", name, force=True, ignore_permissions=True)
		finally:
			frappe.conf.developer_mode, frappe.flags.in_migrate = developer_mode, migrating
		manifest = json.loads(frappe.db.get_single_value("Transport Settings", MANIFEST_FIELD))
		manifest["state"] = "retired"
		manifest["retired"] = True
		frappe.db.set_single_value("Transport Settings", MANIFEST_FIELD, preparation._json(manifest))
		frappe.clear_cache()
		result = verify()
		preparation._conflict(not result["retired"], "Delivery Order metadata was not fully retired")
		return {**result, "source_tables_retained": True, "ready_for_native_only": True}
	except Exception:
		frappe.db.rollback(save_point="delivery_order_retirement")
		frappe.clear_cache()
		raise


def guard_upgrade():
	"""Do not let model sync/orphan cleanup erase unconverted site history."""
	if any(
		frappe.db.exists("DocType", dt)
		for dt in (*RETIRED_TYPES, "Transport Requirement", "Transport Requirement Item")
	):
		raise preparation.MigrationConflict(
			"Delivery Order retirement must be applied and verified before upgrading. Pause writers, confirm backups, run delivery_order_retirement.install_schema, inspect and retire. No historical documents have been deleted."
		)
