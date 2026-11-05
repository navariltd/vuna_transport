"""Warehouse-held depot return demand; onboard cargo is not picked up twice."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt


def posted_pickup_quantities(item_names, exclude_entry=None):
	if not item_names:
		return {}
	return dict(
		frappe.db.sql(
			"""select d.vuna_return_request_item, sum(d.transfer_qty)
		from `tabStock Entry Detail` d join `tabStock Entry` s on s.name=d.parent
		where s.docstatus=1 and s.add_to_transit=1 and s.vuna_return_pickup_proof is not null
		and s.name != %(exclude)s
		and d.vuna_return_request_item in %(items)s group by d.vuna_return_request_item""",
			{"items": tuple(item_names), "exclude": exclude_entry or ""},
		)
	)


def build_depot_return_legs(source):
	from vuna_transport.services.demand.transport_demand_service import get_source_key

	if source.docstatus != 1 or source.return_context != "Depot":
		frappe.throw(_("Only submitted depot Return Requests can generate depot pickup demand."))
	if frappe.db.exists(
		"Transport Trip Task",
		{
			"return_request": source.name,
			"status": ["not in", ["Completed", "Completed with Variance", "Failed", "Cancelled"]],
		},
	):
		frappe.throw(
			_("Remove the direct pickup assignment before creating pickup demand for this Return Request.")
		)
	if (
		getattr(source, "proof_of_delivery", None)
		and frappe.db.get_value("Proof of Delivery", source.proof_of_delivery, "proof_type")
		== "Unplanned Collection"
	):
		# Physical collection happened already. Never fabricate a second pickup.
		return []
	if not source.return_warehouse:
		frappe.throw(_("Select the receiving Return Warehouse before planning the return."))
	destination = frappe.get_doc("Warehouse", source.return_warehouse)
	if destination.get("is_rejected_warehouse") and "nl_vms" in frappe.get_installed_apps():
		frappe.throw(
			_(
				"Select a holding Returns / Quarantine Warehouse, not this site's automatic write-off warehouse."
			)
		)
	posted = posted_pickup_quantities([row.name for row in source.items])
	from vuna_transport.services.execution.unplanned_pickup_service import unplanned_collected_quantities

	unplanned = unplanned_collected_quantities(source)
	legs = defaultdict(list)
	for row in source.items:
		origin = row.custody_warehouse or source.source_warehouse
		if not origin:
			frappe.throw(_("Every return item needs a known custody warehouse before pickup planning."))
		# These goods are already on a vehicle. Phase 6 pickup work must not
		# create another Add-to-Transit receipt for them.
		if frappe.db.get_value("Warehouse", origin, "warehouse_type") == "Transit":
			continue
		if origin == source.return_warehouse:
			frappe.throw(_("Return pickup and receiving warehouses must be different."))
		factor = flt(row.conversion_factor)
		if factor <= 0:
			frappe.throw(_("Return items require a positive UOM conversion factor."))
		quantity = max(
			flt(row.requested_return_qty) * factor - flt(posted.get(row.name)) - flt(unplanned.get(row.name)),
			0,
		)
		if quantity:
			legs[origin].append(
				{
					"source_item": row.name,
					"item_code": row.item_code,
					"transaction_qty": quantity / factor,
					"transaction_uom": row.uom,
					"conversion_factor": factor,
					"stock_qty": quantity,
					"stock_uom": row.stock_uom,
				}
			)
	return [
		{
			"company": source.company,
			"movement_type": "Warehouse Transfer",
			"source_document_type": "Return Request",
			"source_document": source.name,
			"source_key": get_source_key("Return Request", source.name, origin, source.return_warehouse),
			"source_warehouse": origin,
			"destination_warehouse": source.return_warehouse,
			"required_date": source.initiation_date,
			"items": items,
		}
		for origin, items in sorted(legs.items())
	]


def is_return_load(load):
	names = {row.transport_demand for row in load.delivery_orders if row.get("transport_demand")}
	return bool(names) and all(
		frappe.db.get_value("Transport Demand", name, "source_document_type") == "Return Request"
		for name in names
	)


def unpicked_releases(exclude_load=None):
	"""Release planned-but-uncollected quantities after a confirmed pickup attempt."""
	return dict(
		frappe.db.sql(
			"""select li.transport_demand_item,
		sum(greatest(li.allocated_stock_qty - pi.delivered_qty * di.conversion_factor, 0))
		from `tabTransport Trip Task` t
		join `tabProof of Delivery` p on p.transport_trip_task=t.name and p.docstatus=1
		join `tabProof of Delivery Item` pi on pi.parent=p.name
		join `tabTransport Demand Item` di on di.parent=t.transport_demand and di.source_item=pi.source_row
		join `tabTransport Load Item` li on li.parent=t.transport_load and li.transport_demand_item=di.name
		join `tabTransport Load` l on l.name=li.parent and l.docstatus=1
		where t.task_type='Return Pickup' and t.status != 'Cancelled'
		and li.parent != %(exclude)s group by li.transport_demand_item""",
			{"exclude": exclude_load or ""},
		)
	)
