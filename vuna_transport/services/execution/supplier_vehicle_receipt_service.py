"""Company-owned supplier collections enter inventory through Purchase Receipt.

These are warehouse/procurement actions, not driver stock-posting endpoints.
ERPNext remains responsible for purchasing quantities, valuation, QI and ledgers.
"""

import math

import frappe
from frappe import _
from frappe.utils import flt

from vuna_transport.services.shared.security_service import (
	require_doctype_permission,
	require_document_permission,
)

CONTEXT_FIELDS = (
	"vuna_pickup_purchase_order",
	"vuna_pickup_allocation",
	"vuna_pickup_transit_warehouse",
	"vuna_pickup_task",
)


def receipt_context(source_name, allocation_name, *, check_permissions=True):
	from vuna_transport.services.demand.source_policy_service import require_source_enabled
	from vuna_transport.services.demand.supplier_pickup_demand_service import build_supplier_pickup_legs
	from vuna_transport.services.execution.allocation_transfer_service import (
		resolve_vehicle_transit_warehouse,
	)

	order = frappe.get_doc("Purchase Order", source_name)
	allocation = frappe.get_doc("Vehicle Allocation", allocation_name)
	for doc in (order, allocation):
		if check_permissions:
			require_document_permission(doc, "read")
	frappe.db.get_value("Purchase Order", order.name, "name", for_update=True)
	frappe.db.get_value("Vehicle Allocation", allocation.name, "name", for_update=True)
	order.reload()
	allocation.reload()
	require_source_enabled(order)
	if (
		allocation.docstatus != 1
		or allocation.company != order.company
		or not allocation.vehicle
		or not allocation.driver
		or allocation.status in ("Cancelled", "Replaced", "Completed")
	):
		frappe.throw(
			_(
				"Choose an active submitted Vehicle Allocation in the Purchase Order company, with a vehicle and driver."
			)
		)
	transit = resolve_vehicle_transit_warehouse(allocation)
	if frappe.get_doc("Warehouse", transit).get("is_rejected_warehouse"):
		frappe.throw(_("The vehicle transit warehouse must hold inventory, not automatically write it off."))
	legs = build_supplier_pickup_legs(order)
	return order, allocation, transit, legs


def reserved_purchase_quantities(order_name, exclude_receipt=None):
	"""Reserve only this collection path's drafts; submitted receipts update PO."""
	return dict(
		frappe.db.sql(
			"""select i.purchase_order_item, sum(i.qty)
		from `tabPurchase Receipt Item` i join `tabPurchase Receipt` r on r.name=i.parent
		where r.docstatus=0 and r.vuna_pickup_purchase_order=%s and r.name!=%s
		group by i.purchase_order_item""",
			(order_name, exclude_receipt or ""),
		)
	)


@frappe.whitelist()
def create_vehicle_purchase_receipt(
	source_name: str, allocation_name: str, task_name: str | None = None
) -> str:
	"""Map PO -> reviewable PR into vehicle stock. Never submits inventory."""
	from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

	from vuna_transport.services.execution.allocation_transfer_service import set_allocation_stock_defaults

	require_doctype_permission("Purchase Receipt", "create")
	task = None
	if task_name:
		from vuna_transport.services.execution.transfer_execution_service import _lock_task

		task = _lock_task(task_name)
		require_document_permission(task, "read")
	order, allocation, transit, legs = receipt_context(source_name, allocation_name)
	limits = None
	if task:
		from vuna_transport.services.execution.direct_supplier_pickup_service import receipt_task_limits

		limits = receipt_task_limits(task, order, allocation)
	if existing := frappe.db.get_value(
		"Purchase Receipt",
		{
			"vuna_pickup_purchase_order": order.name,
			"vuna_pickup_allocation": allocation.name,
			"vuna_pickup_task": task.name if task else ["is", "not set"],
			"docstatus": 0,
		},
		"name",
	):
		require_document_permission(frappe.get_doc("Purchase Receipt", existing), "read")
		return existing
	if task is None and frappe.db.sql(
		"""select t.name from `tabTransport Trip Task` t
		join `tabTransport Demand` d on d.name=t.transport_demand
		where d.source_document_type='Purchase Order' and d.source_document=%s
		and t.direct_assignment=1 and t.status not in ('Cancelled', 'Completed', 'Completed with Variance') limit 1""",
		order.name,
	):
		frappe.throw(_("Create this collection's Purchase Receipt from its assigned supplier pickup task."))
	reserved = reserved_purchase_quantities(order.name)
	remaining = {
		item["source_item"]: max(item["transaction_qty"] - flt(reserved.get(item["source_item"])), 0)
		for leg in legs
		for item in leg["items"]
	}
	remaining = {key: value for key, value in remaining.items() if value > 0}
	if limits is not None:
		remaining = {key: min(value, limits[key]) for key, value in remaining.items() if key in limits}
	if not remaining:
		frappe.throw(
			_(
				"No outstanding supplier collection quantity remains. Review existing Purchase Receipts and drafts."
			)
		)
	receipt = make_purchase_receipt(order.name, args={"filtered_children": list(remaining)})
	receipt.set("items", [row for row in receipt.items if row.purchase_order_item in remaining])
	receipt.vuna_pickup_purchase_order = order.name
	receipt.vuna_pickup_task = task.name if task else None
	receipt.vuna_pickup_allocation = allocation.name
	receipt.vuna_pickup_transit_warehouse = transit
	receipt.set_warehouse = transit
	receipt.apply_putaway_rule = 0
	receipt.rejected_warehouse = None
	for row in receipt.items:
		row.vuna_pickup_destination_warehouse = row.warehouse
		row.warehouse, row.rejected_warehouse = transit, None
		row.qty = row.received_qty = remaining[row.purchase_order_item]
		row.stock_qty = row.received_stock_qty = row.qty * row.conversion_factor
		row.rejected_qty = 0
	set_allocation_stock_defaults(receipt, allocation)
	receipt.insert()
	return receipt.name


def validate_vehicle_purchase_receipt(doc, method=None):
	previous = doc.get_doc_before_save()
	if previous and any(previous.get(field) != doc.get(field) for field in CONTEXT_FIELDS):
		frappe.throw(
			_("Supplier vehicle receipt lineage cannot be changed. Cancel and recreate the receipt.")
		)
	if doc.get("vuna_pickup_task") and not doc.get("vuna_pickup_purchase_order"):
		frappe.throw(
			_("A supplier pickup task requires its collection Purchase Order and vehicle allocation.")
		)
	if not any(doc.get(field) for field in CONTEXT_FIELDS[1:]):
		# The standard PO mapper auto-fills Link fields targeting Purchase Order;
		# this alone must not opt an ordinary receipt into the vehicle workflow.
		doc.vuna_pickup_purchase_order = None
		return
	if not doc.get("vuna_pickup_purchase_order"):
		return
	if previous and previous.docstatus == 1:
		return  # Submitted stock cannot be rewritten through update-after-submit.
	if doc.get("vuna_pickup_task"):
		from vuna_transport.services.execution.transfer_execution_service import _lock_task

		_lock_task(doc.vuna_pickup_task)
	order, allocation, transit, legs = receipt_context(
		doc.vuna_pickup_purchase_order,
		doc.vuna_pickup_allocation,
		check_permissions=not getattr(doc, "flags", {}).get("ignore_permissions"),
	)
	if (
		doc.company != order.company
		or doc.supplier != order.supplier
		or doc.is_return
		or doc.vuna_pickup_transit_warehouse != transit
		or doc.set_warehouse not in (None, "", transit)
	):
		frappe.throw(
			_(
				"Supplier collection must receive the original supplier's goods into the allocation's vehicle transit warehouse."
			)
		)
	available = {item["source_item"]: item for leg in legs for item in leg["items"]}
	limits = None
	if doc.get("vuna_pickup_task"):
		from vuna_transport.services.execution.direct_supplier_pickup_service import receipt_task_limits

		limits = receipt_task_limits(
			frappe.get_doc("Transport Trip Task", doc.vuna_pickup_task), order, allocation
		)
	reserved = reserved_purchase_quantities(order.name, doc.name)
	seen = set()
	for row in doc.items:
		if limits is not None and (
			row.purchase_order_item not in limits or flt(row.qty) > limits[row.purchase_order_item]
		):
			frappe.throw(_("Purchase Receipt quantities must not exceed this supplier pickup task."))
		source = available.get(row.purchase_order_item)
		qty = flt(row.qty)
		if (
			not source
			or row.purchase_order_item in seen
			or row.purchase_order != order.name
			or row.item_code != source["item_code"]
			or row.uom != source["transaction_uom"]
			or row.stock_uom != source["stock_uom"]
			or flt(row.conversion_factor) != source["conversion_factor"]
			or row.warehouse != transit
			or flt(row.rejected_qty)
			or not math.isfinite(qty)
			or qty <= 0
			or flt(
				qty - max(source["transaction_qty"] - flt(reserved.get(row.purchase_order_item)), 0),
				row.precision("qty"),
			)
			> 0
		):
			frappe.throw(
				_(
					"Vehicle Purchase Receipt rows must retain PO item/UOM lineage and cannot exceed the unreceived, unreserved quantity. Receive only goods actually collected into vehicle stock."
				)
			)
		seen.add(row.purchase_order_item)
	if not seen:
		frappe.throw(_("Add at least one collected Purchase Order item."))


def guard_vehicle_receipt_cancellation(doc, method=None):
	if doc.get("vuna_pickup_purchase_order") and frappe.db.exists(
		"Stock Entry", {"vuna_supplier_purchase_receipt": doc.name, "docstatus": ["!=", 2]}
	):
		frappe.throw(
			_(
				"Cancel or delete the linked vehicle-to-warehouse transfers before cancelling the supplier Purchase Receipt."
			)
		)
