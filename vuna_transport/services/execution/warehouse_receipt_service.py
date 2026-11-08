"""Warehouse-posted End Transit as evidence for Material Request depot tasks.

Stock and request quantities remain ERPNext-owned. This module only validates
task attribution and derives execution/audit state; it never fabricates a POD.
"""

import math
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt


def tasks_for_outgoing(name):
	if not name:
		return []
	names = frappe.db.sql_list(
		"""select distinct t.name from `tabTransport Trip Task` t
		join `tabTransport Trip Task Item` ti on ti.parent=t.name
		join `tabStock Entry Detail` outgoing on outgoing.vuna_transport_load_item=ti.load_item
		join `tabTransport Demand` d on d.name=t.transport_demand
		join `tabDelivery Trip` trip on trip.name=t.delivery_trip
		where outgoing.parent=%s and t.task_type='Transfer Receipt'
		and t.status!='Cancelled' and trip.docstatus=1
		and d.source_document_type='Material Request' and d.movement_type='Warehouse Transfer'
		order by t.name""",
		name,
	)
	return [frappe.get_doc("Transport Trip Task", task) for task in names]


def posted_receipts(outgoing, exclude=None):
	return frappe.db.sql(
		"""select i.*, e.name as receipt_name, e.company as receipt_company, e.modified_by as receipt_user,
		e.modified as receipt_time from `tabStock Entry Detail` i
		join `tabStock Entry` e on e.name=i.parent
		where e.outgoing_stock_entry=%(outgoing)s and i.against_stock_entry=%(outgoing)s
		and e.docstatus=1 and e.add_to_transit=0 and e.purpose='Material Transfer'
		and coalesce(e.vuna_transport_proof,'')='' and e.name!=%(exclude)s
		order by e.modified desc, e.name, i.idx""",
		{"outgoing": outgoing, "exclude": exclude or ""},
		as_dict=True,
	)


def receipt_progress(expected, received, precision):
	if expected and all(flt(received.get(key, 0) - qty, precision) == 0 for key, qty in expected.items()):
		return "Completed"
	return "Partially Received" if any(received.values()) else "Ready"


def receipt_summary(task):
	empty = {
		"status": "Ready",
		"confirmation_method": None,
		"receipt_stock_entry": None,
		"receipt_confirmed_by": None,
		"receipt_confirmed_on": None,
	}
	if task.task_type != "Transfer Receipt" or not getattr(task, "transport_demand", None):
		return empty
	demand = frappe.get_doc("Transport Demand", task.transport_demand)
	if demand.source_document_type != "Material Request" or demand.movement_type != "Warehouse Transfer":
		return empty
	from vuna_transport.services.execution.transfer_execution_service import get_dispatch_entry

	name = get_dispatch_entry(task.name)
	if not name:
		return empty
	stock = frappe.get_doc("Stock Entry", name)
	if stock.docstatus != 1 or not stock.add_to_transit or stock.company != task.company:
		return empty
	expected = {item.load_item: flt(item.stock_qty) for item in task.items}
	sources = {row.name: row for row in stock.items}
	received, evidence = defaultdict(float), []
	for row in posted_receipts(stock.name):
		source = sources.get(row.ste_detail)
		if (
			not source
			or row.receipt_company != task.company
			or source.vuna_transport_load_item not in expected
			or row.s_warehouse != stock.to_warehouse
			or row.t_warehouse != demand.destination_warehouse
			or row.item_code != source.item_code
			or flt(row.transfer_qty) <= 0
		):
			continue
		received[source.vuna_transport_load_item] += flt(row.transfer_qty)
		evidence.append(row)
	if not evidence:
		return empty
	last = evidence[0]
	return {
		"status": receipt_progress(
			expected, received, frappe.get_precision("Stock Entry Detail", "transfer_qty")
		),
		"confirmation_method": "Warehouse Receipt",
		"receipt_stock_entry": last.receipt_name,
		"receipt_confirmed_by": last.receipt_user,
		"receipt_confirmed_on": last.receipt_time,
	}


def assert_no_manual_receipt(task):
	if receipt_summary(task)["confirmation_method"]:
		frappe.throw(
			_(
				"This task already has a warehouse receipt. Receive any outstanding quantities through End Transit; do not post a second receipt through POD."
			)
		)


def validate_warehouse_receipt(doc, method=None):
	if (
		doc.get("vuna_transport_proof")
		or not doc.outgoing_stock_entry
		or doc.add_to_transit
		or doc.purpose != "Material Transfer"
	):
		return
	tasks = tasks_for_outgoing(doc.outgoing_stock_entry)
	if not tasks:
		return
	from vuna_transport.services.execution.transfer_execution_service import _lock_task, get_dispatch_entry
	from vuna_transport.services.execution.transfer_proof_service import stock_identities

	tasks = [_lock_task(task.name) for task in tasks]
	stock = frappe.get_doc("Stock Entry", doc.outgoing_stock_entry)
	if stock.docstatus != 1 or not stock.add_to_transit or stock.company != doc.company:
		frappe.throw(_("End Transit must reference its submitted same-company Add-to-Transit Stock Entry."))
	by_cargo = {}
	for task in tasks:
		if get_dispatch_entry(task.name) != stock.name or task.company != doc.company:
			continue
		demand = frappe.get_doc("Transport Demand", task.transport_demand)
		for item in task.items:
			demand_item = item.transport_demand_item or frappe.db.get_value(
				"Transport Load Item", item.load_item, "transport_demand_item"
			)
			if item.load_item in by_cargo:
				frappe.throw(
					_(
						"Multiple active receiving tasks claim the same cargo. Reconcile the trip assignments first."
					)
				)
			by_cargo[item.load_item] = (
				task,
				demand,
				next((row.source_item for row in demand.items if row.name == demand_item), None),
			)
	sources = {row.name: row for row in stock.items}
	quantities, serials = defaultdict(float), set()
	precision = frappe.get_precision("Stock Entry Detail", "transfer_qty")
	for row in posted_receipts(stock.name, exclude=doc.name):
		for batch, identity in stock_identities(row).items():
			quantities[(row.ste_detail, batch)] += identity["stock_qty"]
			serials.update(identity["serials"])
	for row in doc.items:
		source = sources.get(row.ste_detail)
		if not source or row.against_stock_entry != stock.name:
			frappe.throw(_("Every End Transit row must retain its exact outgoing stock row."))
		context = by_cargo.get(source.vuna_transport_load_item)
		if not context:
			continue
		task, demand, source_item = context
		if frappe.db.exists("Proof of Delivery", {"transport_trip_task": task.name, "docstatus": 1}):
			frappe.throw(
				_("This task already has submitted POD. Use its linked End Transit workflow instead.")
			)
		if (
			row.item_code != source.item_code
			or row.uom != source.uom
			or row.stock_uom != source.stock_uom
			or flt(row.conversion_factor) != flt(source.conversion_factor)
			or row.s_warehouse != stock.to_warehouse
			or row.t_warehouse != demand.destination_warehouse
			or source.material_request != demand.source_document
			or source.material_request_item != source_item
			or not all(
				math.isfinite(flt(value)) for value in (row.qty, row.transfer_qty, row.conversion_factor)
			)
			or flt(row.qty) <= 0
			or flt(row.transfer_qty - row.qty * row.conversion_factor, precision)
		):
			frappe.throw(
				_(
					"Warehouse receipt must preserve the assigned item, requested UOM, depot and Material Request lineage."
				)
			)
		# ERPNext's End Transit rows deliberately do not repeat fulfillment links.
		# Repeating these would count both transport legs against the request.
		row.material_request = row.material_request_item = None
		allowed = stock_identities(source)
		for batch, identity in stock_identities(row).items():
			original = allowed.get(batch)
			quantities[(source.name, batch)] += identity["stock_qty"]
			if (
				not original
				or flt(quantities[(source.name, batch)] - original["stock_qty"], precision) > 0
				or not set(identity["serials"]).issubset(original["serials"])
				or len(identity["serials"]) != len(set(identity["serials"]))
				or (original["serials"] and len(identity["serials"]) != identity["stock_qty"])
				or serials.intersection(identity["serials"])
			):
				frappe.throw(
					_(
						"Warehouse receipt batch/serial quantities must remain within the original dispatched cargo."
					)
				)
			serials.update(identity["serials"])


def sync_warehouse_receipt(doc, method=None):
	if doc.get("vuna_transport_proof") or not doc.outgoing_stock_entry or doc.docstatus not in (1, 2):
		return
	from vuna_transport.services.execution.trip_task_service import sync_task_execution

	tasks = tasks_for_outgoing(doc.outgoing_stock_entry)
	for trip in sorted({task.delivery_trip for task in tasks}):
		sync_task_execution(next(task for task in tasks if task.delivery_trip == trip))
	for task in tasks:
		summary = receipt_summary(task)
		frappe.db.set_value(
			"Transport Trip Task",
			task.name,
			{key: value for key, value in summary.items() if key != "status"},
			update_modified=False,
		)
