"""One customer execution path with explicit native/legacy reference columns.

Never put a Transport Demand name in a Delivery Order Link field. Legacy records
remain readable during migration; new execution uses canonical demand links.
"""

from math import isfinite

import frappe
from frappe import _
from frappe.utils import flt


def native_row(row):
	return bool(getattr(row, "transport_demand", None) and not getattr(row, "delivery_order", None))


def order_name(row):
	return getattr(row, "delivery_order", None) or getattr(row, "transport_demand", None)


def item_name(row):
	return getattr(row, "delivery_order_item", None) or getattr(row, "transport_demand_item", None)


def order_type(row):
	return "Transport Demand" if native_row(row) else "Delivery Order"


def item_type(row):
	return "Transport Demand Item" if native_row(row) else "Delivery Order Item"


def get_item(row):
	return frappe.get_doc(item_type(row), item_name(row))


def order_column(native):
	return "transport_demand" if native else "delivery_order"


def note_column(native):
	return "vuna_transport_demand" if native else "vuna_delivery_order"


def native_load(load):
	from vuna_transport.services.planning.customer_demand_load_service import is_customer_demand_load

	return is_customer_demand_load(load)


def set_note_reference(note, load, name):
	note.set(note_column(native_load(load)), name)
	if native_load(load):
		note.vuna_delivery_order = None


def validate_customer_note(doc, base_load):
	"""Enforce exact row/UOM/company lineage and cumulative non-cancelled qty.

	Rows, not single-value parent backlinks, are authoritative for consolidated
	notes spanning more than one load/demand. Lock every affected load before sums.
	"""
	from vuna_transport.services.execution.dispatch_guard_service import (
		get_locked_delivery_load,
		validate_load_is_operational,
	)

	base_rows = {row.name: row for row in base_load.delivery_orders}
	for item in doc.items:
		if not item.vuna_delivery_load_order:
			matches = [row for row in base_rows.values() if get_item(row).sales_order_item == item.so_detail]
			if len(matches) != 1:
				frappe.throw(_("Delivery Note items require unambiguous Transport Load Item lineage."))
			item.vuna_delivery_load_order = matches[0].name
	keys = {item.vuna_delivery_load_order for item in doc.items}
	rows = {
		row.name: row
		for row in frappe.get_all("Transport Load Item", filters={"name": ["in", list(keys)]}, fields=["*"])
	}
	if set(rows) != keys:
		frappe.throw(_("A referenced Transport Load Item does not exist."))
	loads = {name: get_locked_delivery_load(name) for name in sorted({r.parent for r in rows.values()})}
	if base_load.name not in loads:
		frappe.throw(_("Delivery Note header load must own at least one item."))
	if len(loads) > 1:
		allocations = {load.active_vehicle_allocation for load in loads.values()}
		if len(allocations) != 1 or not next(iter(allocations)):
			frappe.throw(_("Consolidated Delivery Note loads must share an active Vehicle Allocation."))
	for load in loads.values():
		validate_load_is_operational(load, _("validating customer Delivery Notes"))
		if load.docstatus != 1 or load.company != doc.company:
			frappe.throw(_("Delivery Note requires submitted loads in its company."))
	current = {}
	native_names, legacy_names = set(), set()
	for item in doc.items:
		row = rows[item.vuna_delivery_load_order]
		source_item = get_item(row)
		source = frappe.get_doc(order_type(row), order_name(row))
		if native_row(row) and (
			source.movement_type != "Customer Delivery" or source.source_document_type != "Sales Order"
		):
			frappe.throw(_("Warehouse transfer demand cannot be delivered using a customer Delivery Note."))
		if (
			source_item.parent != source.name
			or source.docstatus != 1
			or source.company != doc.company
			or source.customer != doc.customer
		):
			frappe.throw(_("Customer source lineage does not match this Delivery Note."))
		if (
			item.so_detail != source_item.sales_order_item
			or item.against_sales_order != source.sales_order
			or item.item_code != source_item.item_code
		):
			frappe.throw(_("Delivery Note item must match its exact allocated Sales Order row."))
		if (
			item.uom != row.uom
			or item.stock_uom != row.stock_uom
			or not isfinite(flt(item.conversion_factor))
			or abs(flt(item.conversion_factor) - flt(row.conversion_factor)) > 1e-9
		):
			frappe.throw(_("Delivery Note must preserve its allocated UOM and conversion factor."))
		if doc.shipping_address_name != row.delivery_address:
			frappe.throw(_("Delivery Note address does not match its allocated destination."))
		if frappe.db.get_value("Warehouse", item.warehouse, "company") != doc.company:
			frappe.throw(_("Delivery Note warehouses must belong to its company."))
		if not isfinite(flt(item.qty)) or flt(item.qty) <= 0:
			frappe.throw(_("Allocated customer delivery quantities must be positive."))
		current[row.name] = current.get(row.name, 0) + flt(item.qty)
		(native_names if native_row(row) else legacy_names).add(source.name)
	other = dict(
		frappe.db.sql(
			"""select i.vuna_delivery_load_order, sum(i.qty)
		from `tabDelivery Note Item` i join `tabDelivery Note` n on n.name=i.parent
		where n.docstatus!=2 and n.is_return=0 and n.name!=%(current)s
		and i.vuna_delivery_load_order in %(rows)s group by i.vuna_delivery_load_order""",
			{"current": doc.name or "", "rows": tuple(keys)},
		)
	)
	for key, qty in current.items():
		if qty + flt(other.get(key)) > flt(rows[key].allocated_quantity) + 1e-6:
			frappe.throw(
				_("Cumulative Delivery Note quantity exceeds Transport Load Item {0} allocation.").format(key)
			)
	if doc.get("vuna_transport_demand") and doc.vuna_transport_demand not in native_names:
		frappe.throw(_("Delivery Note Transport Demand does not own its items."))
	doc.vuna_transport_demand = next(iter(native_names)) if len(native_names) == 1 else None
	if not legacy_names:
		doc.vuna_delivery_order = None


def native_note(doc):
	if doc.get("vuna_transport_demand") and not doc.get("vuna_delivery_order"):
		return True
	names = [item.vuna_delivery_load_order for item in doc.items if item.vuna_delivery_load_order]
	return bool(
		names
		and frappe.db.exists(
			"Transport Load Item",
			{"name": ["in", names], "transport_demand": ["is", "set"], "delivery_order": ["is", "not set"]},
		)
	)


def note_submission_status(load):
	from vuna_transport.services.execution.lineage_service import get_delivery_notes_for_loads

	rows = list(load.delivery_orders)
	expected = {row.transport_demand for row in rows}
	names = get_delivery_notes_for_loads([load.name])
	notes = frappe.get_all("Delivery Note", filters={"name": ["in", names]}, fields=["name", "docstatus"])
	active = [n for n in notes if n.docstatus != 2]
	submitted = [n.name for n in active if n.docstatus == 1]
	actual = dict(
		frappe.db.sql(
			"""select i.vuna_delivery_load_order, sum(i.qty)
	from `tabDelivery Note Item` i join `tabDelivery Note` n on n.name=i.parent
	where n.docstatus=1 and n.is_return=0 and i.vuna_delivery_load_order in %(rows)s
	group by i.vuna_delivery_load_order""",
			{"rows": tuple(r.name for r in rows) or ("",)},
		)
	)
	covered = {
		name
		for name in expected
		if all(
			abs(flt(actual.get(r.name)) - flt(r.allocated_quantity)) <= 1e-6
			for r in rows
			if r.transport_demand == name
		)
	}
	drafts = sorted(n.name for n in active if n.docstatus == 0)
	return {
		"expected_delivery_orders": sorted(expected),
		"submitted_delivery_orders": sorted(covered),
		"missing_delivery_orders": sorted(expected - covered),
		"draft_delivery_notes": drafts,
		"active_delivery_notes": sorted(n.name for n in active),
		"submitted_delivery_notes": sorted(submitted),
		"ready": bool(rows) and not (expected - covered) and not drafts,
	}


def populate_native_pod(pod):
	"""Bind every proof row to its DN/load/source row; never trust a client hash."""
	from vuna_transport.services.execution.stock_identity_service import get_single_delivery_note_item_batch

	note = frappe.get_doc("Delivery Note", pod.delivery_note)
	if not native_note(note):
		return False
	if note.docstatus != 1 or note.company != pod.company or note.customer != pod.customer:
		frappe.throw(_("Customer proof must match its submitted Delivery Note and company."))
	if len(pod.items) != len(note.items) or {r.delivery_note_item for r in pod.items} != {
		r.name for r in note.items
	}:
		frappe.throw(_("Keep every Delivery Note item once when recording customer proof."))
	by_note = {row.name: row for row in note.items}
	parents = set()
	for row in pod.items:
		item = by_note[row.delivery_note_item]
		allocation = frappe.get_doc("Transport Load Item", item.vuna_delivery_load_order)
		if native_row(allocation):
			if (
				row.get("transport_demand_item")
				and row.transport_demand_item != allocation.transport_demand_item
			):
				frappe.throw(_("Proof item Transport Demand Item does not match the delivered allocation."))
			row.transport_demand_item = allocation.transport_demand_item
			row.delivery_order_item = None
			parents.add(allocation.transport_demand)
		else:
			row.delivery_order_item = allocation.delivery_order_item
		row.item_code, row.item_name = item.item_code, item.item_name
		row.uom, row.stock_uom, row.conversion_factor = item.uom, item.stock_uom, item.conversion_factor
		row.ordered_qty = abs(flt(item.qty))
		row.batch_no = row.batch_no or get_single_delivery_note_item_batch(item)
		row.source_bundle = item.serial_and_batch_bundle
	pod.transport_demand = next(iter(parents)) if len(parents) == 1 else None
	return True


def recompute_native_demand(name):
	"""Per-item completion across partial loads/UOMs; no inventory or accounting."""
	from vuna_transport.services.planning.demand_lifecycle_service import allocation_status

	demand = frappe.get_doc("Transport Demand", name)
	if getattr(demand, "migration_archived", False):
		return
	if demand.docstatus != 1 or demand.source_document_type != "Sales Order":
		return
	states = [
		frappe._dict(stock_qty=r.stock_qty, allocated=r.allocated_stock_qty, reserved=r.reserved_stock_qty)
		for r in demand.items
	]
	status = allocation_status(states)
	proofs = frappe.db.sql(
		"""select evidence.transport_demand_item, sum(evidence.covered) covered, sum(evidence.accepted) accepted from (
	select i.transport_demand_item, i.ordered_qty*i.conversion_factor covered,
	i.delivered_qty*i.conversion_factor accepted
	from `tabProof of Delivery Item` i join `tabProof of Delivery` p on p.name=i.parent
	where p.docstatus=1 and p.proof_type='Customer Delivery' and i.transport_demand_item in %(items)s
	union all
	select l.transport_demand_item, n.stock_qty covered, n.stock_qty accepted
	from `tabDelivery Note Item` n join `tabDelivery Note` d on d.name=n.parent
	join `tabTransport Load Item` l on l.name=n.vuna_delivery_load_order
	join `tabTransport Load` t on t.name=l.parent
	join `tabDelivery Method` m on m.name=t.delivery_method
	where d.docstatus=1 and d.is_return=0 and t.docstatus=1 and t.status='Completed'
	and coalesce(m.requires_delivery_trip,0)=0 and l.transport_demand_item in %(items)s
	and not exists (select 1 from `tabProof of Delivery Item` pi join `tabProof of Delivery` pp on pp.name=pi.parent
	where pp.docstatus=1 and pi.delivery_note_item=n.name)
	) evidence group by evidence.transport_demand_item""",
		{"items": tuple(r.name for r in demand.items) or ("",)},
		as_dict=True,
	)
	by_item = {r.transport_demand_item: r for r in proofs}
	if proofs:
		status = (
			"Delivered"
			if all(
				flt(by_item.get(r.name, {}).get("covered")) + 1e-6 >= flt(r.stock_qty)
				and flt(by_item.get(r.name, {}).get("accepted")) + 1e-6 >= flt(r.stock_qty)
				and not flt(r.reserved_stock_qty)
				for r in demand.items
			)
			else "Partially Delivered"
		)
	elif frappe.db.sql(
		"""select l.name from `tabTransport Load Item` i join `tabTransport Load` l on l.name=i.parent
	where i.transport_demand=%s and l.docstatus=1 and l.status in ('Dispatched','Completed','Completed with Exceptions') limit 1""",
		name,
	):
		status = "Dispatched"
	elif frappe.db.get_value("Sales Order", demand.source_document, "status") in ("Closed", "Cancelled"):
		status = "Closed"
	frappe.db.set_value("Transport Demand", name, "status", status, update_modified=False)


def recompute_pod_demands(pod):
	names = [
		row.transport_demand_item
		for row in (pod.get("items") or [])
		if getattr(row, "transport_demand_item", None) and not getattr(row, "delivery_order_item", None)
	]
	if names:
		_recompute_pod_loads(pod)
	for name in (
		frappe.get_all(
			"Transport Demand Item", filters={"name": ["in", names]}, pluck="parent", distinct=True
		)
		if names
		else []
	):
		recompute_native_demand(name)


def _recompute_pod_loads(pod):
	"""A consolidated note/proof may complete more than its single header load."""
	from vuna_transport.services.execution.dispatch_guard_service import set_delivery_load_status

	loads = frappe.db.sql_list(
		"""select distinct l.parent from `tabDelivery Note Item` n
	join `tabTransport Load Item` l on l.name=n.vuna_delivery_load_order where n.parent=%s""",
		pod.delivery_note,
	)
	for name in loads:
		rows = frappe.get_all(
			"Transport Load Item", filters={"parent": name}, fields=["name", "allocated_quantity"]
		)
		outcomes = frappe.db.sql(
			"""select n.vuna_delivery_load_order as row_name,
		sum(i.ordered_qty) covered, sum(i.delivered_qty) accepted
		from `tabProof of Delivery Item` i join `tabProof of Delivery` p on p.name=i.parent
		join `tabDelivery Note Item` n on n.name=i.delivery_note_item
		where p.docstatus=1 and p.proof_type='Customer Delivery' and n.vuna_delivery_load_order in %(rows)s
		group by n.vuna_delivery_load_order""",
			{"rows": tuple(r.name for r in rows) or ("",)},
			as_dict=True,
		)
		by_row = {r.row_name: r for r in outcomes}
		complete = bool(rows) and all(
			flt(by_row.get(r.name, {}).get("covered")) + 1e-6 >= flt(r.allocated_quantity) for r in rows
		)
		if complete:
			accepted = all(flt(by_row[r.name].accepted) + 1e-6 >= flt(r.allocated_quantity) for r in rows)
			current = frappe.db.get_value("Transport Load", name, "status")
			set_delivery_load_status(
				name,
				"Completed" if accepted else "Completed with Exceptions",
				_("Customer proof quantities reconciled across note item lineage."),
				force=current in ("Completed", "Completed with Exceptions"),
			)
		elif frappe.db.get_value("Transport Load", name, "status") in (
			"Completed",
			"Completed with Exceptions",
		):
			set_delivery_load_status(
				name, "Dispatched", _("Customer proof was cancelled; delivery is pending again."), force=True
			)
