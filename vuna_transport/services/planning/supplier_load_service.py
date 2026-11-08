"""Plan supplier collections from external addresses, without inventing stock."""

import math

import frappe
from frappe import _
from frappe.utils import flt

from vuna_transport.services.shared.security_service import require_company_access


@frappe.whitelist()
def get_load_items(transport_demand: str) -> list:
	from vuna_transport.services.shared.security_service import require_doctype_permission

	require_doctype_permission("Transport Load", "create")
	return available_items(frappe.get_doc("Transport Demand", transport_demand))


@frappe.whitelist()
def get_planning_items(
	company: str, date_from: str | None = None, date_to: str | None = None, start: int = 0
) -> dict:
	from frappe.utils import getdate

	from vuna_transport.services.shared.security_service import require_doctype_permission

	require_doctype_permission("Transport Demand", "read")
	require_company_access(company)
	if date_from and date_to and getdate(date_from) > getdate(date_to):
		frappe.throw(_("Required date range is invalid."))
	filters = {
		"company": company,
		"docstatus": 1,
		"movement_type": "Supplier Pickup",
		"status": ["not in", ["Cancelled", "Closed"]],
	}
	if date_from:
		filters["required_date"] = [">=", date_from]
	if date_to:
		filters["required_date"] = ["between", [date_from, date_to]] if date_from else ["<=", date_to]
	names = frappe.get_list(
		"Transport Demand",
		filters=filters,
		pluck="name",
		order_by="required_date asc, name asc",
		limit_start=max(start, 0),
		limit_page_length=21,
	)
	rows = []
	for name in names[:20]:
		muted = frappe.flags.mute_messages
		try:
			frappe.flags.mute_messages = True
			rows.extend(available_items(frappe.get_doc("Transport Demand", name)))
		except (frappe.ValidationError, frappe.PermissionError):
			continue
		finally:
			frappe.flags.mute_messages = muted
	return {"items": rows[:500], "has_more": len(names) > 20, "truncated": len(rows) > 500}


def uncollected_releases(exclude_load=None):
	return dict(
		frappe.db.sql(
			"""select li.transport_demand_item, sum(greatest(li.allocated_stock_qty-pi.stock_qty,0))
		from `tabTransport Trip Task` t join `tabProof of Delivery` p on p.transport_trip_task=t.name and p.docstatus=1
		join `tabProof of Delivery Item` pi on pi.parent=p.name
		join `tabTransport Demand Item` di on di.parent=t.transport_demand and di.source_item=pi.source_row
		join `tabTransport Load Item` li on li.parent=t.transport_load and li.transport_demand_item=di.name
		join `tabTransport Load` l on l.name=li.parent and l.docstatus=1
		where t.task_type='Supplier Pickup' and t.status!='Cancelled' and li.parent!=%s
		group by li.transport_demand_item""",
			exclude_load or "",
		)
	)


def balances(demand, exclude_load=None):
	from vuna_transport.services.execution.supplier_receipt_confirmation_service import task_progress
	from vuna_transport.services.planning.transport_load_service import get_committed_quantities

	committed = get_committed_quantities([item.name for item in demand.items], exclude_load)
	for item, qty in uncollected_releases(exclude_load).items():
		if item in committed:
			committed[item] = max(flt(committed[item]) - flt(qty), 0)
	posted = {}
	# ERPNext's PO received_qty already includes these receipts. Reuse the
	# task allocator (including ordinary staff receipts), never subtract twice.
	for name in frappe.get_all(
		"Transport Trip Task",
		filters={
			"transport_demand": demand.name,
			"task_type": "Supplier Pickup",
			"status": ["!=", "Cancelled"],
			"transport_load": ["!=", exclude_load or ""],
		},
		pluck="name",
	):
		task = frappe.get_doc("Transport Trip Task", name)
		state = task_progress(task)
		items = {item.name: item for item in demand.items}
		for cargo in task.items:
			item = items.get(cargo.transport_demand_item)
			if item:
				posted[item.name] = posted.get(item.name, 0) + min(
					flt(cargo.stock_qty), flt(state["received"].get(item.source_item))
				)
	return committed, posted


def source_context(demand):
	from vuna_transport.services.demand.delivery_method_service import get_delivery_method_workflow
	from vuna_transport.services.demand.source_policy_service import require_source_enabled
	from vuna_transport.services.demand.supplier_pickup_demand_service import build_supplier_pickup_legs

	demand.check_permission("read")
	require_company_access(demand.company)
	if demand.get("migration_archived"):
		frappe.throw(_("Historical-only demand cannot create new collection work."))
	if (
		demand.docstatus != 1
		or demand.movement_type != "Supplier Pickup"
		or demand.source_document_type != "Purchase Order"
		or demand.status in ("Cancelled", "Closed")
	):
		frappe.throw(_("Select an open submitted supplier pickup demand."))
	order = frappe.get_doc("Purchase Order", demand.source_document, for_update=True)
	order.check_permission("read")
	require_source_enabled(order)
	workflow = get_delivery_method_workflow(order.vuna_delivery_method)
	if workflow.disabled or not workflow.requires_transport_planning:
		frappe.throw(
			_(
				"This supplier Delivery Method does not use load planning. Assign its demand directly to a trip."
			)
		)
	leg = next(
		(leg for leg in build_supplier_pickup_legs(order) if leg["source_key"] == demand.source_key), None
	)
	if (
		not leg
		or leg["company"] != demand.company
		or leg["source_address"] != demand.source_address
		or leg["destination_warehouse"] != demand.destination_warehouse
	):
		frappe.throw(_("The supplier demand no longer matches its Purchase Order pickup and destination."))
	return order, {item["source_item"]: item for item in leg["items"]}


def available_items(demand, exclude_load=None):
	from vuna_transport.services.planning.transport_load_service import available_stock_quantity

	order, live = source_context(demand)
	committed, posted = balances(demand, exclude_load)
	result = []
	for item in demand.items:
		source = live.get(item.source_item)
		if not source:
			continue
		if (
			item.item_code != source["item_code"]
			or item.transaction_uom != source["transaction_uom"]
			or item.stock_uom != source["stock_uom"]
			or flt(item.conversion_factor) != flt(source["conversion_factor"])
		):
			frappe.throw(_("Supplier demand items must retain the Purchase Order item and UOM."))
		qty = available_stock_quantity(
			item.stock_qty, source["stock_qty"], committed.get(item.name), posted.get(item.name)
		)
		if qty > 0:
			result.append(
				{
					"transport_demand": demand.name,
					"transport_demand_item": item.name,
					"item_code": item.item_code,
					"item_name": frappe.get_cached_value("Item", item.item_code, "item_name"),
					"available_quantity": qty / flt(item.conversion_factor),
					"allocated_quantity": qty / flt(item.conversion_factor),
					"uom": item.transaction_uom,
					"supplier": order.supplier,
					"source_address": demand.source_address,
					"destination_warehouse": demand.destination_warehouse,
				}
			)
	return result


def validate_supplier_allocations(load):
	from vuna_transport.services.demand.delivery_order_service import (
		_get_group_transport_requirements,
		_get_item_transport_measurements,
	)
	from vuna_transport.services.planning.load_validation_service import (
		validate_transport_compatibility_groups,
	)
	from vuna_transport.services.shared.measurement_service import get_transport_uoms

	names = sorted({row.transport_demand for row in load.delivery_orders if row.transport_demand})
	if not names or any(not row.transport_demand or row.delivery_order for row in load.delivery_orders):
		frappe.throw(_("Select Transport Demand items for this supplier load."))
	# Source locks precede demand locks, matching source cancellation/generation.
	demands = {name: frappe.get_doc("Transport Demand", name) for name in names}
	for order in sorted({d.source_document for d in demands.values()}):
		frappe.db.get_value("Purchase Order", order, "name", for_update=True)
	frappe.db.sql(
		"select name from `tabTransport Demand` where name in %(names)s order by name for update",
		{"names": tuple(names)},
	)
	for demand in demands.values():
		demand.reload()
	data, contexts = {}, {}
	for demand in demands.values():
		order, _live = source_context(demand)
		contexts[demand.name] = order
		data[demand.name] = {row["transport_demand_item"]: row for row in available_items(demand, load.name)}
	if (
		len(
			{
				(
					d.company,
					contexts[d.name].supplier,
					d.source_address,
					contexts[d.name].vuna_delivery_method,
				)
				for d in demands.values()
			}
		)
		!= 1
	):
		frappe.throw(
			_(
				"Supplier loads must share a company, supplier, pickup address and Delivery Method. Use separate loads on the same allocation for different pickups."
			)
		)
	first = next(iter(demands.values()))
	if load.company != first.company or load.source_warehouse:
		frappe.throw(
			_("Supplier collections use the supplier pickup address, not a company source warehouse.")
		)
	load.movement_type, load.supplier, load.source_address = (
		"Supplier Pickup",
		contexts[first.name].supplier,
		first.source_address,
	)
	load.delivery_method = contexts[first.name].vuna_delivery_method
	seen, cargo = set(), []
	for row in load.delivery_orders:
		demand = demands[row.transport_demand]
		item = next((item for item in demand.items if item.name == row.transport_demand_item), None)
		available = data[demand.name].get(row.transport_demand_item)
		qty = flt(row.allocated_quantity)
		if (
			not item
			or not available
			or item.name in seen
			or not math.isfinite(qty)
			or qty <= 0
			or flt(
				(qty - available["available_quantity"]) * flt(item.conversion_factor),
				row.precision("allocated_stock_qty"),
			)
			> 0
		):
			frappe.throw(
				_(
					"Supplier load quantities must be positive, unique and within the unallocated Purchase Order balance."
				)
			)
		stock_qty = flt(qty * flt(item.conversion_factor), row.precision("allocated_stock_qty"))
		if stock_qty <= 0:
			frappe.throw(_("The allocation is below stock quantity precision."))
		for uom, amount in ((item.transaction_uom, qty), (item.stock_uom, stock_qty)):
			if (
				frappe.get_cached_value("UOM", uom, "must_be_whole_number")
				and abs(amount - round(amount)) > 1e-9
			):
				frappe.throw(_("{0} requires whole-number allocations.").format(uom))
		for field in ("item_code", "stock_uom", "delivery_group", "transport_compatibility_group"):
			row.set(field, item.get(field))
		row.item_name, row.uom, row.available_quantity = (
			available["item_name"],
			item.transaction_uom,
			available["available_quantity"],
		)
		row.conversion_factor, row.allocated_stock_qty = item.conversion_factor, stock_qty
		row.destination_warehouse = demand.destination_warehouse
		row.delivery_order = row.delivery_order_item = row.sales_order = row.customer = (
			row.delivery_address
		) = None
		measurements = _get_item_transport_measurements(item, qty)
		row.weight, row.weight_uom = measurements.weight, get_transport_uoms().weight_uom
		cargo.append((item, qty, measurements))
		seen.add(item.name)
	validate_transport_compatibility_groups(load.delivery_orders)
	load.update(
		_get_group_transport_requirements([(item, qty) for item, qty, _m in cargo], company=load.company)
	)
	uoms = get_transport_uoms()
	load.weight_uom, load.volume_uom = uoms.weight_uom, uoms.volume_uom
	load.total_weight = sum(m.weight for _i, _q, m in cargo)
	load.total_volume = sum(m.volume for _i, _q, m in cargo)
	load.total_pallets = load.total_cases = load.total_crates = 0
	load.total_stops = 1
	load.minimum_payload = load.total_weight


def supplier_tasks(load):
	from vuna_transport.services.execution.trip_task_service import task_key

	if load.docstatus != 1:
		frappe.throw(_("Submit supplier loads before planning their trip."))
	groups = {}
	for row in load.delivery_orders:
		groups.setdefault(row.transport_demand, []).append(row)
	specs = []
	for name, rows in groups.items():
		demand = frappe.get_doc("Transport Demand", name)
		demand.check_permission("read")
		if (
			demand.docstatus != 1
			or demand.movement_type != "Supplier Pickup"
			or demand.company != load.company
			or demand.source_address != load.source_address
		):
			frappe.throw(_("Supplier demand must match its submitted load and pickup address."))
		items = {item.name: item for item in demand.items}
		cargo = []
		for row in rows:
			item = items.get(row.transport_demand_item)
			if not item or item.item_code != row.item_code or flt(row.allocated_quantity) <= 0:
				frappe.throw(_("Supplier load item lineage is invalid."))
			cargo.append(
				{
					"load_item": row.name,
					"transport_demand_item": item.name,
					"item_code": row.item_code,
					"qty": row.allocated_quantity,
					"uom": item.transaction_uom,
					"conversion_factor": item.conversion_factor,
					"stock_qty": row.allocated_stock_qty,
					"stock_uom": item.stock_uom,
				}
			)
		specs.append(
			{
				"task_type": "Supplier Pickup",
				"transport_load": load.name,
				"transport_demand": demand.name,
				"address": demand.source_address,
				"delivery_note": None,
				"items": cargo,
				"task_key": task_key("Supplier Pickup", load.name, demand.name),
			}
		)
	return specs
