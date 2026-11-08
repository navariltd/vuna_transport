"""Explicit staging preparation, not a patch and not the Delivery Order cutover.

Run through bench execute as Administrator. Default is a read-only preflight.
No business hooks, stock postings, source edits or internal commits are allowed.
"""

import copy
import hashlib
import json
import math
from collections import Counter

import frappe
from frappe.utils import get_time

from vuna_transport.services.demand.customer_demand_fields import (
	CUSTOMER_EXECUTION_STATUSES,
	ITEM_FIELDS,
	ROOT_FIELDS,
	customer_fields,
)
from vuna_transport.services.demand.transport_demand_service import get_source_key
from vuna_transport.services.planning.demand_lifecycle_service import allocation_status

# Old links remain authoritative until the customer execution path is ported.
LINKS = (
	("Transport Load Item", "delivery_order", "transport_demand", False),
	("Transport Load Item", "delivery_order_item", "transport_demand_item", True),
	("Delivery Note", "vuna_delivery_order", "vuna_transport_demand", False),
	("Delivery Stop", "vuna_delivery_order", "vuna_transport_demand", False),
	("Proof of Delivery", "delivery_order", "transport_demand", False),
	("Proof of Delivery Item", "delivery_order_item", "transport_demand_item", True),
	("Return Request", "delivery_order", "transport_demand", False),
	("Return Request Item", "delivery_order_item", "transport_demand_item", True),
)
AUDIT_LINKS = (
	("Comment", "reference_doctype", "reference_name"),
	("Communication", "reference_doctype", "reference_name"),
	("File", "attached_to_doctype", "attached_to_name"),
	("ToDo", "reference_type", "reference_name"),
	("Version", "ref_doctype", "docname"),
	("DocShare", "share_doctype", "share_name"),
)
LEDGERS = ("Stock Ledger Entry", "GL Entry")
MIGRATION_FIELDS = (
	"legacy_delivery_order",
	"legacy_delivery_order_checksum",
	"legacy_delivery_order_snapshot",
)
NUMERIC_ITEM_FIELDS = ("transaction_qty", "conversion_factor", "stock_qty")
TEXT_ITEM_FIELDS = ("item_code", "transaction_uom", "stock_uom")


class MigrationConflict(Exception):
	"""Requires operator review; never silently repair operational data."""


def _json(value):
	return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def checksum(value):
	return hashlib.sha256(_json(value).encode()).hexdigest()


def _same_number(left, right):
	return math.isclose(float(left or 0), float(right or 0), rel_tol=1e-9, abs_tol=1e-6)


def _conflict(condition, message):
	if condition:
		raise MigrationConflict(message)


def needs_uom_recovery(source):
	return any(
		not row.get("uom") or not row.get("stock_uom") or row.get("conversion_factor") in (None, "", 0)
		for row in source["items"]
	)


def _sales_order_for_recovery(source):
	if not needs_uom_recovery(source):
		return None
	name = source["document"].get("sales_order")
	_conflict(
		not name or not frappe.db.exists("Sales Order", name),
		f"{source['document']['name']}: UOM recovery requires the exact original Sales Order",
	)
	return frappe.get_doc("Sales Order", name).as_dict()


def recover_missing_uoms(source, sales_order=None):
	"""Resolve absent units into a copy, never modify original evidence.

	The exact Sales Order child supplies the historical transaction UOM/factor,
	not today's Item conversion table. Existing contradictory values still block.
	"""
	resolved = copy.deepcopy(source)
	recoveries = []
	if not needs_uom_recovery(source):
		return resolved, recoveries
	order = source["document"]
	label = order["name"]
	_conflict(
		not sales_order or sales_order.get("name") != order.get("sales_order"),
		f"{label}: UOM recovery requires the exact original Sales Order",
	)
	_conflict(
		sales_order.get("company") != order.get("company")
		or sales_order.get("customer") != order.get("customer")
		or sales_order.get("docstatus") not in (1, 2),
		f"{label}: UOM recovery Sales Order company/customer/submitted history does not match",
	)
	originals = {row["name"]: row for row in sales_order.get("items", [])}
	for row in resolved["items"]:
		if row.get("uom") and row.get("stock_uom") and row.get("conversion_factor") not in (None, "", 0):
			continue
		name = row["name"]
		origin = originals.get(row.get("sales_order_item"))
		_conflict(
			not origin
			or origin.get("item_code") != row.get("item_code")
			or origin.get("parent") != sales_order["name"]
			or origin.get("parenttype") != "Sales Order",
			f"{label}/{name}: UOM recovery requires an exact matching Sales Order Item",
		)
		_conflict(
			sum(r.get("sales_order_item") == origin["name"] for r in resolved["items"]) != 1,
			f"{label}/{name}: duplicate Sales Order row requires reviewed UOM recovery",
		)
		try:
			qty, original_qty, factor, original_stock, old_factor, old_stock = (
				float(v or 0)
				for v in (
					row.get("transport_qty"),
					origin.get("qty"),
					origin.get("conversion_factor"),
					origin.get("stock_qty"),
					row.get("conversion_factor"),
					row.get("stock_qty"),
				)
			)
		except (TypeError, ValueError) as exc:
			raise MigrationConflict(f"{label}/{name}: invalid numeric UOM recovery evidence") from exc
		stock = qty * factor
		_conflict(
			not all(
				math.isfinite(v)
				for v in (qty, original_qty, factor, original_stock, old_factor, old_stock, stock)
			)
			or factor <= 0
			or qty < 0
			or original_qty < qty
			or old_factor < 0
			or old_stock < 0
			or not _same_number(original_qty * factor, original_stock)
			or not origin.get("uom")
			or not origin.get("stock_uom"),
			f"{label}/{name}: inconsistent Sales Order UOM recovery evidence",
		)
		_conflict(
			row.get("uom") not in (None, "", origin["uom"])
			or row.get("stock_uom") not in (None, "", origin["stock_uom"])
			or (old_factor and not _same_number(old_factor, factor))
			or (old_stock and not _same_number(old_stock, stock)),
			f"{label}/{name}: existing UOM/quantity contradicts the Sales Order; review required",
		)
		fields = ("uom", "conversion_factor", "stock_uom", "stock_qty")
		before = {field: row.get(field) for field in fields}
		row.update(
			uom=origin["uom"], conversion_factor=factor, stock_uom=origin["stock_uom"], stock_qty=stock
		)
		recoveries.append(
			{
				"delivery_order_item": name,
				"sales_order": sales_order["name"],
				"sales_order_item": origin["name"],
				"before": before,
				"resolved": {field: row[field] for field in fields},
				"sales_order_evidence": {
					"company": sales_order["company"],
					"customer": sales_order["customer"],
					"docstatus": sales_order["docstatus"],
					"qty": original_qty,
					"uom": origin["uom"],
					"conversion_factor": factor,
					"stock_qty": original_stock,
					"stock_uom": origin["stock_uom"],
				},
			}
		)
	return resolved, json.loads(_json(recoveries))


def prepared_source_for_validation(source, previous):
	"""Rebuild a prepared draft from verified recovery evidence, not client data.

	The original DO deliberately remains incomplete. A normal target draft save
	must therefore use an in-memory source projection rather than undo the repair.
	"""
	if not previous or not getattr(previous, "legacy_delivery_order_snapshot", None):
		return source
	try:
		snapshot = json.loads(previous.legacy_delivery_order_snapshot)
	except (TypeError, ValueError) as exc:
		raise MigrationConflict("Invalid persisted migration snapshot; review required") from exc
	_conflict(not isinstance(snapshot, dict), "Invalid persisted migration snapshot; review required")
	if not snapshot.get("uom_recoveries"):
		return source
	_conflict(
		previous.source_document_type != "Delivery Order"
		or previous.source_document != source.name
		or previous.legacy_delivery_order != source.name,
		"Prepared UOM recovery belongs to another source",
	)
	raw = _source(source.name)
	_conflict(
		checksum(raw) != previous.legacy_delivery_order_checksum,
		"Original Delivery Order changed after UOM recovery; review required",
	)
	resolved, recoveries = recover_missing_uoms(raw, _sales_order_for_recovery(raw))
	_conflict(
		recoveries != snapshot["uom_recoveries"], "Sales Order UOM recovery evidence changed; review required"
	)
	values = source.as_dict()
	by_name = {row["name"]: row for row in resolved["items"]}
	for row in values["items"]:
		row.update(
			{key: by_name[row["name"]][key] for key in ("uom", "conversion_factor", "stock_uom", "stock_qty")}
		)
	return frappe.get_doc(values)


def build_copy(source, existing=None, sales_order=None):
	"""Pure, deterministic projection. Keep historical rows, including zero quantities.

	The complete source, custom fields and old row identities live in the snapshot;
	this projection only supplies the existing generic Transport Demand schema.
	"""

	order = source["document"]
	effective, recoveries = recover_missing_uoms(source, sales_order)
	rows = effective["items"]
	name = order["name"]
	_conflict(not order.get("company"), f"{name}: missing Company")
	_conflict(not rows, f"{name}: no Delivery Order items")
	_conflict(order.get("docstatus") not in (0, 1, 2), f"{name}: invalid docstatus")
	_conflict(
		any(not order.get(f) for f in ("customer", "delivery_address", "source_warehouse")),
		f"{name}: incomplete customer, delivery address or source warehouse; review before preparation",
	)
	by_source = {}
	if existing:
		if existing.get("legacy_delivery_order_snapshot"):
			try:
				saved_snapshot = json.loads(existing["legacy_delivery_order_snapshot"])
			except (TypeError, ValueError) as exc:
				raise MigrationConflict(f"{name}: invalid migration recovery snapshot") from exc
			_conflict(not isinstance(saved_snapshot, dict), f"{name}: invalid migration recovery snapshot")
			saved_recoveries = saved_snapshot.get("uom_recoveries", [])
			_conflict(
				saved_recoveries != recoveries,
				f"{name}: Sales Order UOM recovery evidence changed; review required",
			)
		_conflict(
			existing.get("source_document_type") != "Delivery Order"
			or existing.get("source_document") != name
			or existing.get("company") != order["company"],
			f"{name}: existing demand has different source/company",
		)
		_conflict(
			existing.get("legacy_delivery_order") not in (None, "", name),
			f"{name}: existing demand already belongs to another migration source",
		)
		_conflict(
			existing.get("movement_type") != "Customer Delivery"
			or existing.get("demand_type") not in (None, "", "Selling"),
			f"{name}: existing demand is not a customer-selling movement",
		)
		_conflict(
			order["docstatus"] == 0 and existing.get("docstatus") != 0,
			f"{name}: existing demand is submitted but source is a draft",
		)
		_conflict(
			existing.get("docstatus") == 2 and order["docstatus"] != 2,
			f"{name}: existing demand is cancelled but source is not",
		)
		_conflict(
			order["docstatus"] == 2 and existing.get("docstatus") != 2,
			f"{name}: source is cancelled but existing demand is not",
		)
		for item in existing.get("items", []):
			_conflict(item["source_item"] in by_source, f"{name}: duplicate demand source row")
			_conflict(
				item.get("parent") != existing["name"] or item.get("parenttype") != "Transport Demand",
				f"{name}: invalid existing demand item parent",
			)
			by_source[item["source_item"]] = item
		_conflict(
			set(by_source) != {r["name"] for r in rows},
			f"{name}: existing demand and Delivery Order have different row sets",
		)

	demand = {
		"doctype": "Transport Demand",
		"name": existing["name"] if existing else name,
		"company": order["company"],
		"movement_type": "Customer Delivery",
		"demand_type": "Selling",
		"source_document_type": "Delivery Order",
		"source_document": name,
		"source_key": get_source_key(
			"Delivery Order", name, order.get("source_warehouse"), order.get("delivery_address")
		),
		"source_warehouse": order.get("source_warehouse"),
		"destination_address": order.get("delivery_address"),
		"customer": order.get("customer"),
		"required_date": order.get("required_delivery_date"),
		"transport_zone": order.get("transport_zone"),
		"docstatus": existing["docstatus"] if existing else order["docstatus"],
		"legacy_delivery_order": name,
		"legacy_delivery_order_checksum": checksum(source),
	}
	demand.update(customer_fields(order, ROOT_FIELDS))
	if existing:
		_check_customer_field_conflicts(existing, demand, ROOT_FIELDS, name)
	if existing:
		for field in (
			"source_key",
			"source_warehouse",
			"destination_address",
			"customer",
			"required_date",
			"transport_zone",
		):
			_conflict(
				str(existing.get(field) or "") != str(demand.get(field) or ""),
				f"{name}: existing demand {field} differs from source",
			)
		_conflict(
			existing.get("legacy_delivery_order_checksum")
			and existing["legacy_delivery_order_checksum"] != demand["legacy_delivery_order_checksum"],
			f"{name}: source changed after preparation; reviewed refresh/cutover required",
		)
	items = []
	states = []
	for row in rows:
		qty = float(row.get("transport_qty") or 0)
		factor = float(row.get("conversion_factor") or 0)
		stock = float(row.get("stock_qty") or 0)
		_conflict(
			not all(math.isfinite(v) for v in (qty, factor, stock))
			or factor <= 0
			or qty < 0
			or stock < 0
			or not _same_number(qty * factor, stock),
			f"{name}/{row['name']}: inconsistent quantity/UOM conversion",
		)
		_conflict(
			any(not row.get(f) for f in ("item_code", "uom", "stock_uom")),
			f"{name}/{row['name']}: missing Item or UOM",
		)
		_conflict(
			row.get("parent") != name or row.get("parenttype") != "Delivery Order",
			f"{name}/{row['name']}: invalid source row parent",
		)
		_conflict(
			row.get("s_warehouse") and row["s_warehouse"] != order["source_warehouse"],
			f"{name}/{row['name']}: multiple loading warehouses require a reviewed leg mapping",
		)
		previous = by_source.get(row["name"])
		item = {
			"doctype": "Transport Demand Item",
			"name": previous["name"] if previous else row["name"],
			"parent": demand["name"],
			"parenttype": "Transport Demand",
			"parentfield": "items",
			"idx": row["idx"],
			"source_item": row["name"],
			"item_code": row["item_code"],
			"transaction_qty": row.get("transport_qty"),
			"transaction_uom": row["uom"],
			"conversion_factor": row["conversion_factor"],
			"stock_qty": row["stock_qty"],
			"stock_uom": row["stock_uom"],
			"delivery_group": row.get("delivery_group") or order.get("delivery_group"),
			"transport_compatibility_group": row.get("transport_compatibility_group")
			or order.get("transport_compatibility_group"),
			"weight": row.get("weight"),
			"weight_uom": row.get("weight_uom"),
			"reserved_stock_qty": float(row.get("reserved_quantity") or 0) * factor,
			"allocated_stock_qty": float(row.get("allocated_quantity") or 0) * factor,
		}
		item.update(customer_fields(row, ITEM_FIELDS))
		item["remaining_stock_qty"] = max(stock - item["reserved_stock_qty"] - item["allocated_stock_qty"], 0)
		if previous:
			_check_customer_field_conflicts(previous, item, ITEM_FIELDS, f"{name}/{row['name']}")
			for field in NUMERIC_ITEM_FIELDS:
				_conflict(
					not _same_number(previous.get(field), item[field]),
					f"{name}/{row['name']}: {field} differs",
				)
			for field in TEXT_ITEM_FIELDS:
				_conflict(previous.get(field) != item[field], f"{name}/{row['name']}: {field} differs")
		for field in ("owner", "creation", "modified", "modified_by"):
			item[field] = row.get(field)
		item["docstatus"] = demand["docstatus"]
		items.append(item)
		states.append(
			frappe._dict(
				stock_qty=stock, reserved=item["reserved_stock_qty"], allocated=item["allocated_stock_qty"]
			)
		)
	demand["status"] = (
		"Draft"
		if demand["docstatus"] == 0
		else "Cancelled"
		if demand["docstatus"] == 2
		else allocation_status(states)
	)
	if demand["docstatus"] == 1 and order.get("status") in CUSTOMER_EXECUTION_STATUSES:
		demand["status"] = order["status"]
	for field in ("owner", "creation", "modified", "modified_by"):
		demand[field] = order.get(field)
	return {
		"demand": demand,
		"items": items,
		"existing": bool(existing),
		"source": source,
		"uom_recoveries": recoveries,
	}


def _check_customer_field_conflicts(existing, expected, fields, label):
	for field in fields:
		current = existing.get(field)
		# The additive fields on old wrappers have database defaults, not an
		# intentional customer-planning value. Never replace a conflicting value.
		if current not in (None, "", 0) and not _customer_value_equal(field, current, expected.get(field)):
			_conflict(True, f"{label}: customer planning field {field} differs from source")


def _customer_value_equal(field, actual, expected):
	if field in ("delivery_from_time", "delivery_to_time"):
		if actual in (None, "") or expected in (None, ""):
			return actual in (None, "") and expected in (None, "")
		return get_time(actual) == get_time(expected)
	if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
		return _same_number(actual, expected)
	return str(actual or "") == str(expected or "")


def _source(name):
	source = {
		"document": frappe.db.get_value("Delivery Order", name, "*", as_dict=True),
		"items": frappe.get_all(
			"Delivery Order Item",
			filters={"parent": name, "parenttype": "Delivery Order", "parentfield": "items"},
			fields=["*"],
			order_by="idx, name",
		),
	}
	# Preserve site-specific child tables as well, without pretending the generic
	# Transport Demand form already implements their behaviour.
	source["other_children"] = {
		field.fieldname: frappe.get_all(
			field.options,
			filters={"parent": name, "parenttype": "Delivery Order", "parentfield": field.fieldname},
			fields=["*"],
			order_by="idx, name",
		)
		for field in frappe.get_meta("Delivery Order").get_table_fields()
		if field.fieldname != "items"
	}
	return source


def _audit(name):
	return {
		dt: frappe.get_all(
			dt, filters={type_field: "Delivery Order", name_field: name}, fields=["*"], order_by="name"
		)
		for dt, type_field, name_field in AUDIT_LINKS
	}


def _fingerprint(doctype):
	"""Keyset pagination; ledger values and existing row identities must not change."""
	digest = hashlib.sha256()
	cursor = ""
	count = 0
	while True:
		rows = frappe.get_all(
			doctype,
			fields=["*"],
			filters={"name": [">", cursor]},
			order_by="name",
			limit_page_length=500,
		)
		if not rows:
			break
		for row in rows:
			digest.update((_json(row) + "\n").encode())
		cursor = rows[-1].name
		count += len(rows)
	return {"count": count, "sha256": digest.hexdigest()}


def _authorize(dry_run, backup_confirmed, workers_paused):
	if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
		raise frappe.PermissionError(
			"Only Administrator/System Manager may prepare Delivery Order migration."
		)
	if not all(isinstance(value, bool) for value in (dry_run, backup_confirmed, workers_paused)):
		raise ValueError("dry_run, backup_confirmed and workers_paused must be Python booleans")
	if not dry_run and (not frappe.conf.maintenance_mode or not backup_confirmed or not workers_paused):
		raise MigrationConflict(
			"Apply requires maintenance mode, paused workers/integrations and a confirmed full backup."
		)


def _schema_errors():
	errors = []
	for dt, fields in (("Transport Demand", ROOT_FIELDS), ("Transport Demand Item", ITEM_FIELDS)):
		for field in fields:
			if not frappe.get_meta(dt).has_field(field) or not frappe.db.has_column(dt, field):
				errors.append(f"Deploy/migrate the customer-planning schema first: {dt}.{field}")
	for field in MIGRATION_FIELDS:
		if not frappe.get_meta("Transport Demand").has_field(field) or not frappe.db.has_column(
			"Transport Demand", field
		):
			errors.append(f"Deploy/migrate the preparation schema first: Transport Demand.{field}")
	for dt, old, new, _child in LINKS:
		for field in (old, new):
			if not frappe.get_meta(dt).has_field(field) or not frappe.db.has_column(dt, field):
				errors.append(f"Deploy/migrate the preparation schema first: {dt}.{field}")
	return errors


def _plans(names, errors):
	plans = []
	for name in names:
		try:
			source = _source(name)
			_conflict(not source["document"], f"Delivery Order {name} does not exist")
			candidates = set(
				frappe.get_all(
					"Transport Demand",
					filters={"source_document_type": "Delivery Order", "source_document": name},
					pluck="name",
				)
			) | set(frappe.get_all("Transport Demand", filters={"legacy_delivery_order": name}, pluck="name"))
			_conflict(len(candidates) > 1, f"{name}: ambiguous existing demands: {sorted(candidates)}")
			existing = (
				frappe.get_doc("Transport Demand", next(iter(candidates))).as_dict() if candidates else None
			)
			plan = build_copy(source, existing, sales_order=_sales_order_for_recovery(source))
			if not existing:
				_conflict(frappe.db.exists("Transport Demand", name), f"{name}: target name already in use")
				for item in plan["items"]:
					_conflict(
						frappe.db.exists("Transport Demand Item", item["name"]),
						f"{name}: target row {item['name']} already exists",
					)
			key_owner = frappe.db.get_value(
				"Transport Demand", {"source_key": plan["demand"]["source_key"]}, "name"
			)
			_conflict(
				key_owner and key_owner != plan["demand"]["name"],
				f"{name}: source key belongs to {key_owner}",
			)
			plans.append(plan)
		except MigrationConflict as exc:
			errors.append(str(exc))
	return plans


def _link_changes(plans, errors):
	orders = {p["source"]["document"]["name"]: p["demand"]["name"] for p in plans}
	items = {r["source_item"]: r["name"] for p in plans for r in p["items"]}
	item_parents = {r["source_item"]: p["source"]["document"]["name"] for p in plans for r in p["items"]}
	changes = []
	for dt, old, new, child in LINKS:
		mapping = items if child else orders
		if not mapping:
			continue
		fields = ["name", old, new]
		if dt == "Transport Load Item":
			fields.extend(f for f in ("delivery_order", "delivery_order_item") if f not in fields)
		for row in frappe.get_all(dt, filters={old: ["in", list(mapping)]}, fields=fields):
			if dt == "Transport Load Item":
				expected_parent = item_parents.get(row.get("delivery_order_item"))
				if not expected_parent or expected_parent != row.get("delivery_order"):
					errors.append(
						f"Transport Load Item {row.name}: Delivery Order and item lineage do not match"
					)
					continue
			expected = mapping[row[old]]
			if row.get(new) and row[new] != expected:
				errors.append(f"{dt} {row.name}: {new} already points to {row[new]}, expected {expected}")
			elif not row.get(new):
				changes.append(
					{
						"doctype": dt,
						"name": row.name,
						"old_field": old,
						"old_value": row[old],
						"field": new,
						"value": expected,
					}
				)
	return changes


def _orphan_links(errors):
	"""Full-site scans must not quietly omit references to missing source records."""
	for dt, old, _new, child in LINKS:
		target = "Delivery Order Item" if child else "Delivery Order"
		known = set(frappe.get_all(target, pluck="name"))
		for row in frappe.get_all(dt, filters={old: ["is", "set"]}, fields=["name", old]):
			if row[old] not in known:
				errors.append(f"{dt} {row.name}: orphan {old} link {row[old]}")


def _lock_sources(names):
	# Maintenance mode is necessary but not sufficient: pause workers/integrations too.
	sales_orders = sorted(
		{
			name
			for name in frappe.get_all("Delivery Order", filters={"name": ["in", names]}, pluck="sales_order")
			if name
		}
	)
	for dt, field in (("Sales Order", "name"), ("Sales Order Item", "parent")):
		if sales_orders:
			table = frappe.qb.DocType(dt)
			frappe.qb.from_(table).select(table.name).where(table[field].isin(sales_orders)).orderby(
				table.name
			).for_update().run()
	for dt, field in (("Delivery Order", "name"), ("Delivery Order Item", "parent")):
		table = frappe.qb.DocType(dt)
		frappe.qb.from_(table).select(table.name).where(table[field].isin(names)).orderby(
			table.name
		).for_update().run()
	# Existing canonical targets must not change between preflight and verification.
	for dt in ("Transport Demand", "Transport Demand Item"):
		table = frappe.qb.DocType(dt)
		frappe.qb.from_(table).select(table.name).orderby(table.name).for_update().run()


def _write_plan(plan):
	demand = plan["demand"]
	name = demand["name"]
	if not plan["existing"]:
		# Historical import only. Calling insert/submit would regenerate source rows,
		# reservations or business side effects; these are copied, never replayed.
		frappe.get_doc(demand).db_insert()
		for row in plan["items"]:
			frappe.get_doc(row).db_insert()
	else:
		frappe.db.set_value(
			"Transport Demand", name, customer_fields(demand, ROOT_FIELDS), update_modified=False
		)
		for row in plan["items"]:
			frappe.db.set_value(
				"Transport Demand Item", row["name"], customer_fields(row, ITEM_FIELDS), update_modified=False
			)
	# Do not overwrite already-live target quantities, lifecycle or audit metadata.
	snapshot = {
		"format_version": 2 if plan.get("uom_recoveries") else 1,
		"source": plan["source"],
		"audit": _audit(plan["source"]["document"]["name"]),
		"item_map": {r["source_item"]: r["name"] for r in plan["items"]},
	}
	if plan.get("uom_recoveries"):
		snapshot["uom_recoveries"] = plan["uom_recoveries"]
	frappe.db.set_value(
		"Transport Demand",
		name,
		{
			"legacy_delivery_order": demand["legacy_delivery_order"],
			"legacy_delivery_order_checksum": demand["legacy_delivery_order_checksum"],
			"legacy_delivery_order_snapshot": _json(snapshot),
		},
		update_modified=False,
	)


def _verify(plans, changes):
	for plan in plans:
		expected = plan["demand"]
		_conflict(
			checksum(_source(plan["source"]["document"]["name"]))
			!= expected["legacy_delivery_order_checksum"],
			"Source changed during migration",
		)
		actual = frappe.get_doc("Transport Demand", expected["name"]).as_dict()
		build_copy(plan["source"], actual, sales_order=_sales_order_for_recovery(plan["source"]))
		for field in ROOT_FIELDS:
			_conflict(
				not _customer_value_equal(field, actual.get(field), expected.get(field)),
				f"{expected['name']}: customer field {field} verification failed",
			)
		by_source = {r["source_item"]: r for r in actual["items"]}
		for row in plan["items"]:
			for field in ITEM_FIELDS:
				_conflict(
					not _customer_value_equal(
						field, by_source[row["source_item"]].get(field), row.get(field)
					),
					f"{expected['name']}/{row['name']}: customer field {field} verification failed",
				)
		_conflict(actual["docstatus"] != expected["docstatus"], f"{expected['name']}: docstatus changed")
		_conflict(
			{r["source_item"]: r["name"] for r in actual["items"]}
			!= {r["source_item"]: r["name"] for r in plan["items"]},
			f"{expected['name']}: child identity mapping changed",
		)
		for field in ("legacy_delivery_order", "legacy_delivery_order_checksum"):
			_conflict(
				actual.get(field) != expected[field], f"{expected['name']}: verification failed for {field}"
			)
		snapshot = json.loads(actual["legacy_delivery_order_snapshot"])
		_conflict(
			snapshot.get("uom_recoveries", []) != plan.get("uom_recoveries", []),
			"UOM recovery provenance verification failed",
		)
		_conflict(
			checksum(snapshot["source"]) != expected["legacy_delivery_order_checksum"],
			"Source snapshot verification failed",
		)
		_conflict(
			snapshot["audit"] != json.loads(_json(_audit(plan["source"]["document"]["name"]))),
			"Audit metadata changed during migration",
		)
	for change in changes:
		values = frappe.db.get_value(
			change["doctype"], change["name"], [change["field"], change["old_field"]]
		)
		_conflict(
			tuple(values or ()) != (change["value"], change["old_value"]),
			f"Link verification failed: {change['doctype']} {change['name']}",
		)


def prepare(
	dry_run: bool = True,
	backup_confirmed: bool = False,
	workers_paused: bool = False,
	source_names: list[str] | None = None,
):
	"""Preflight or apply preparation; bench commits only after a successful return.

	Not whitelisted. This does NOT retire Delivery Orders, change source lineage to
	Sales Order, move audit links, delete records or replay stock/accounting events.
	"""
	_authorize(dry_run, backup_confirmed, workers_paused)
	if not frappe.db.exists("DocType", "Delivery Order"):
		from vuna_transport.services.demand.delivery_order_retirement import verify

		return {"mode": "retired", **verify()}
	if source_names is not None and (
		not isinstance(source_names, list) or not all(isinstance(n, str) for n in source_names)
	):
		raise ValueError("source_names must be a list of Delivery Order names")
	names = sorted(
		set(source_names if source_names is not None else frappe.get_all("Delivery Order", pluck="name"))
	)
	errors = _schema_errors()
	report = {
		"mode": "dry_run" if dry_run else "apply",
		"source_count": len(names),
		"whole_site_scope": source_names is None,
		"ready_for_retirement": False,
		"blockers": errors,
		"next_step": "Run delivery_order_cutover_audit.summary and review historical source/UOM/grouping cases before promotion or Delivery Order retirement.",
	}
	if errors:
		if not dry_run:
			raise MigrationConflict(_json(report))
		return report
	if not dry_run and names:
		_lock_sources(names)
	plans = _plans(names, errors)
	changes = _link_changes(plans, errors)
	if source_names is None:
		_orphan_links(errors)
	report.update(
		{
			"create": sum(not p["existing"] for p in plans),
			"reuse": sum(p["existing"] for p in plans),
			"new_links": dict(Counter(c["doctype"] for c in changes)),
			"mapping": {p["source"]["document"]["name"]: p["demand"]["name"] for p in plans},
			"preflight_passed": not errors,
			"legacy_workflow_retained": True,
			"uom_recovered_rows": sum(len(p.get("uom_recoveries", [])) for p in plans),
			"uom_recoveries": {
				p["demand"]["name"]: p["uom_recoveries"] for p in plans if p.get("uom_recoveries")
			},
		}
	)
	if dry_run:
		return report
	if errors:
		raise MigrationConflict(_json(report))
	savepoint = "delivery_order_preparation"
	frappe.db.savepoint(savepoint)
	try:
		before = {dt: _fingerprint(dt) for dt in (*LEDGERS, "Delivery Order", "Delivery Order Item")}
		for plan in plans:
			_write_plan(plan)
		for change in changes:
			frappe.db.set_value(
				change["doctype"], change["name"], change["field"], change["value"], update_modified=False
			)
		_verify(plans, changes)
		after = {dt: _fingerprint(dt) for dt in before}
		_conflict(before != after, "Stock/accounting or original Delivery Order data changed; rolled back")
		report.update({"verified": True, "unchanged_tables": after})
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		raise
	return report


def verify(source_names: list[str] | None = None):
	"""Read-only verification of prepared data, not permission to retire the source.

	The apply report is the contemporaneous ledger checksum evidence. This command
	does not compare ledgers against a historical baseline after normal operations.
	"""
	report = prepare(source_names=source_names)
	if report.get("mode") == "retired":
		return report
	if report.get("blockers"):
		raise MigrationConflict(_json(report))
	if report["create"] or report["new_links"]:
		raise MigrationConflict("Preparation is incomplete: demand copies or shadow links are missing")
	errors = []
	plans = _plans(sorted(report["mapping"]), errors)
	if errors:
		raise MigrationConflict(_json(errors))
	_verify(plans, [])
	report["mode"] = "verify"
	report["prepared_data_verified"] = True
	return report
