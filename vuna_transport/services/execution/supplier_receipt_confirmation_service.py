"""Confirm supplier collection from actual PO receipts, not warehouse transfers.

Explicit task receipts are reserved for that task. Unlinked receipts apply only
to unambiguous matching PO rows assigned before the receipt.
ERPNext remains authoritative for inventory and purchasing.
"""

from collections import defaultdict

import frappe
from frappe.utils import flt, get_datetime


def assigned_items(order):
	return frappe.db.sql(
		"""select t.name as task_name, t.creation as task_creation, t.delivery_trip,
		ti.item_code, ti.stock_uom, ti.stock_qty, di.source_item as po_item
		from `tabTransport Trip Task` t
		join `tabDelivery Trip` trip on trip.name=t.delivery_trip
		join `tabTransport Demand` d on d.name=t.transport_demand
		join `tabTransport Trip Task Item` ti on ti.parent=t.name
		join `tabTransport Demand Item` di on di.name=ti.transport_demand_item and di.parent=d.name
		where t.task_type='Supplier Pickup' and t.status!='Cancelled' and trip.docstatus=1
		and d.docstatus=1 and d.source_document_type='Purchase Order' and d.source_document=%s
		and t.company=d.company and trip.company=d.company
		order by t.creation, t.name, ti.idx""",
		order,
		as_dict=True,
	)


def posted_items(order):
	return frappe.db.sql(
		"""select r.name as receipt_name, r.vuna_pickup_task as explicit_task,
		r.modified as receipt_time, r.modified_by as receipt_user,
		i.purchase_order_item as po_item, i.item_code, i.stock_uom,
		i.received_qty * i.conversion_factor as stock_qty
		from `tabPurchase Receipt Item` i join `tabPurchase Receipt` r on r.name=i.parent
		join `tabPurchase Order` po on po.name=i.purchase_order
		join `tabPurchase Order Item` poi on poi.name=i.purchase_order_item and poi.parent=po.name
		where i.purchase_order=%s and r.docstatus=1 and r.is_return=0
		and r.company=po.company and r.supplier=po.supplier and i.item_code=poi.item_code
		order by r.modified, r.name, i.idx""",
		order,
		as_dict=True,
	)


def allocate_receipts(assignments, receipts, precision):
	"""No receipt quantity may satisfy more than one assignment."""
	states = {}
	expected = defaultdict(float)
	for item in assignments:
		expected[(item.task_name, item.po_item)] += flt(item.stock_qty)
		states.setdefault(item.task_name, {"received": defaultdict(float), "evidence": None})
	# Reserve explicitly attributed receipts before allocating generic PO receipts.
	for receipt in sorted(receipts, key=lambda row: not bool(row.explicit_task)):
		remaining = max(flt(receipt.stock_qty), 0)
		eligible = [
			item
			for item in assignments
			if (
				item.po_item == receipt.po_item
				and item.item_code == receipt.item_code
				and item.stock_uom == receipt.stock_uom
				and (not receipt.explicit_task or receipt.explicit_task == item.task_name)
				and (
					receipt.explicit_task
					or get_datetime(receipt.receipt_time) >= get_datetime(item.task_creation)
				)
				and flt(
					expected[(item.task_name, item.po_item)]
					- states[item.task_name]["received"][item.po_item],
					precision,
				)
				> 0
			)
		]
		if len({item.task_name for item in eligible}) != 1:
			continue  # Never guess which trip collected a shared PO row.
		for item in eligible:
			state = states[item.task_name]
			outstanding = expected[(item.task_name, item.po_item)] - state["received"][item.po_item]
			qty = min(max(outstanding, 0), remaining)
			if flt(qty, precision) <= 0:
				continue
			state["received"][item.po_item] += qty
			remaining -= qty
			if not state["evidence"] or get_datetime(receipt.receipt_time) >= get_datetime(
				state["evidence"].receipt_time
			):
				state["evidence"] = receipt
	for name, state in states.items():
		limits = {source: qty for (task, source), qty in expected.items() if task == name}
		state["status"] = (
			"Completed"
			if limits
			and all(
				flt(qty - state["received"].get(source, 0), precision) == 0 for source, qty in limits.items()
			)
			else "Partially Received"
			if any(state["received"].values())
			else "Ready"
		)
	return states


def order_progress(order):
	return allocate_receipts(
		assigned_items(order), posted_items(order), frappe.get_precision("Purchase Receipt Item", "stock_qty")
	)


def task_progress(task):
	demand = frappe.get_doc("Transport Demand", task.transport_demand)
	return order_progress(demand.source_document).get(
		task.name, {"status": "Ready", "received": {}, "evidence": None}
	)


def sync_receipt(doc, method=None):
	from vuna_transport.services.execution.trip_task_service import sync_task_execution

	orders = {row.purchase_order for row in doc.get("items", []) if row.purchase_order}
	if doc.get("vuna_pickup_purchase_order"):
		orders.add(doc.vuna_pickup_purchase_order)
	for order in sorted(orders):
		items = assigned_items(order)
		if not items:
			continue
		from vuna_transport.services.execution.transfer_execution_service import _lock_task

		for name in sorted({item.task_name for item in items}):
			_lock_task(name)
		states = order_progress(order)
		for name, state in states.items():
			evidence = state["evidence"]
			frappe.db.set_value(
				"Transport Trip Task",
				name,
				{
					"confirmation_method": "Purchase Receipt" if evidence else None,
					"receipt_purchase_receipt": evidence.receipt_name if evidence else None,
					"receipt_confirmed_by": evidence.receipt_user if evidence else None,
					"receipt_confirmed_on": evidence.receipt_time if evidence else None,
				},
				update_modified=False,
			)
		for trip in sorted({item.delivery_trip for item in items}):
			sync_task_execution(
				frappe.get_doc(
					"Transport Trip Task",
					next(item.task_name for item in items if item.delivery_trip == trip),
				)
			)
