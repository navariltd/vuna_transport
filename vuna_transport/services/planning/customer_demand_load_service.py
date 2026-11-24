"""Canonical customer-demand bundling in requested UOM, with live SO limits."""

import math

import frappe
from frappe import _
from frappe.utils import flt

from vuna_transport.services.demand.delivery_method_service import (
	canonical_delivery_method,
	delivery_methods_are_compatible,
)
from vuna_transport.services.demand.sales_transport_demand_service import (
	INACTIVE_SALES_STATUSES,
	sales_source_key,
)
from vuna_transport.services.shared.security_service import require_company_access, require_doctype_permission


class CustomerDemandGroupingChanged(frappe.ValidationError):
	"""The demand snapshot no longer matches its live source grouping."""


def is_customer_demand_load(load):
	return getattr(load, "movement_type", None) == "Customer Delivery" and any(
		row.get("transport_demand") and not row.get("delivery_order") for row in load.delivery_orders
	)


def _read_demand(name, for_execution=False):
	demand = frappe.get_doc("Transport Demand", name)
	demand.check_permission("read")
	if demand.get("migration_archived"):
		frappe.throw(_("Historical-only demand cannot be loaded or dispatched."))
	require_company_access(demand.company)
	if demand.movement_type != "Customer Delivery" or demand.source_document_type != "Sales Order":
		frappe.throw(_("Select customer Transport Demand generated directly from a Sales Order."))
	if demand.docstatus != 1 or demand.status in (
		("Closed", "Cancelled") if for_execution else ("Closed", "Cancelled", "Delivered")
	):
		frappe.throw(_("Submit an open customer Transport Demand before bundling it."))
	return demand


def posted_sales_quantities(item_names, exclude_load=None):
	if not item_names:
		return {}
	return dict(
		frappe.db.sql(
			"""select li.transport_demand_item, sum(dni.stock_qty)
		from `tabDelivery Note Item` dni join `tabDelivery Note` dn on dn.name=dni.parent
		join `tabTransport Load Item` li on li.name=dni.vuna_delivery_load_order
		where dn.docstatus=1 and dn.is_return=0
		and li.transport_demand_item in %(items)s and li.parent!=%(exclude)s
		group by li.transport_demand_item""",
			{"items": tuple(item_names), "exclude": exclude_load or ""},
		)
	)


def available_items(demand, exclude_load=None, lock_source=False, for_execution=False):
	from vuna_transport.services.demand import delivery_order_service as sales
	from vuna_transport.services.planning.transport_load_service import get_committed_quantities

	source = frappe.get_doc("Sales Order", demand.source_document, for_update=lock_source)
	source.check_permission("read")
	if (
		source.company != demand.company
		or source.docstatus != 1
		or source.status
		in (
			tuple(s for s in INACTIVE_SALES_STATUSES if s != "Completed")
			if for_execution
			else INACTIVE_SALES_STATUSES
		)
	):
		frappe.throw(_("The source Sales Order is no longer open for transport planning."))
	by_source = {row.name: row for row in source.items}
	keys = set()
	committed = get_committed_quantities([row.name for row in demand.items], exclude_load)
	posted = posted_sales_quantities([row.name for row in demand.items], exclude_load)
	all_posted = posted_sales_quantities([row.name for row in demand.items]) if for_execution else posted
	result = {}
	for row in demand.items:
		original = by_source.get(row.source_item)
		if not original or original.item_code != row.item_code or row.sales_order_item != original.name:
			frappe.throw(_("Customer demand item lineage no longer matches its Sales Order."))
		factor = flt(row.conversion_factor)
		if (
			not math.isfinite(factor)
			or factor <= 0
			or original.uom != row.transaction_uom
			or original.stock_uom != row.stock_uom
			or abs(flt(original.conversion_factor) - factor) > 1e-9
		):
			frappe.throw(_("Sales Order UOM changed; review this Transport Demand before loading."))
		keys.add(sales._get_group_key(source, original))
		# A direct ERPNext delivery may have reduced outstanding source quantity.
		live = sales._get_transport_quantity(original) * factor
		if for_execution:
			live += max(flt(all_posted.get(row.name)) - flt(posted.get(row.name)), 0)
		pending = max(flt(committed.get(row.name)) - flt(posted.get(row.name)), 0)
		remaining = max(min(flt(row.stock_qty) - flt(committed.get(row.name)), live - pending), 0)
		result[row.name] = {
			"row": row,
			"available_stock_qty": remaining,
			"available_quantity": remaining / factor,
		}
	if (
		len(keys) != 1
		or sales_source_key(source.name, next(iter(keys)), [r.source_item for r in demand.items])
		!= demand.source_key
	):
		frappe.throw(
			_(
				"Transport Demand {0}: Sales Order delivery grouping changed; review the demand before loading."
			).format(demand.name),
			CustomerDemandGroupingChanged,
		)
	return result


@frappe.whitelist()
def get_load_items(transport_demand: str) -> list[dict]:
	require_doctype_permission("Transport Load", "create")
	demand = _read_demand(transport_demand)
	return [
		{
			"transport_demand": demand.name,
			"transport_demand_item": key,
			"item_code": value["row"].item_code,
			"item_name": value["row"].item_name,
			"uom": value["row"].transaction_uom,
			"available_quantity": value["available_quantity"],
			"allocated_quantity": value["available_quantity"],
		}
		for key, value in available_items(demand).items()
		if value["available_quantity"] > 0
	]


def validate_customer_allocations(load, for_execution=False):
	from vuna_transport.services.planning.delivery_load_service import _get_on_hand_stock
	from vuna_transport.services.planning.demand_lifecycle_service import lock_demands
	from vuna_transport.services.planning.load_validation_service import (
		validate_transport_compatibility_groups,
	)

	if not load.delivery_orders or any(
		not row.transport_demand or row.delivery_order for row in load.delivery_orders
	):
		frappe.throw(_("Do not mix legacy sales rows and native customer demand rows in this load."))
	names = sorted({row.transport_demand for row in load.delivery_orders})
	preview = [_read_demand(name, for_execution=for_execution) for name in names]
	for source_name in sorted({d.source_document for d in preview}):
		frappe.db.get_value("Sales Order", source_name, "name", for_update=True)
	lock_demands(names)
	demands = {name: _read_demand(name, for_execution=for_execution) for name in names}
	first = demands[names[0]]
	if len({(d.transport_handling_profile or "").strip() for d in demands.values()}) > 1:
		frappe.throw(_("Customer demands must have compatible handling profiles."))
	if load.company != first.company or any(d.company != load.company for d in demands.values()):
		frappe.throw(_("Customer demands must belong to the load company."))
	if load.source_warehouse:
		warehouse = frappe.get_doc("Warehouse", load.source_warehouse)
		warehouse.check_permission("read")
		if warehouse.company != load.company or warehouse.is_group or warehouse.disabled:
			frappe.throw(_("Select an active loading warehouse in the load company."))
	elif load.docstatus == 1:
		frappe.throw(_("Select a Source Warehouse before submitting this Transport Load."))
	load.movement_type = "Customer Delivery"
	load.delivery_method = canonical_delivery_method(load.delivery_method or first.delivery_method)
	load.dispatch_date = load.dispatch_date or first.required_date
	load.transport_zone = load.transport_zone or first.transport_zone
	load.transport_route = load.transport_route or first.preferred_route
	live = {
		name: available_items(d, load.name, lock_source=True, for_execution=for_execution)
		for name, d in demands.items()
	}
	bins = _get_on_hand_stock(
		[row.item_code for d in demands.values() for row in d.items], load.source_warehouse
	)
	seen = set()
	stops = {}
	body_types = {d.required_body_type for d in demands.values() if d.required_body_type}
	if len(body_types) > 1:
		frappe.throw(_("Customer demands require incompatible vehicle body types."))
	load.required_body_type = next(iter(body_types), None)
	load.refrigeration_required = any(d.refrigeration_required for d in demands.values())
	load.tail_lift_required = any(d.tail_lift_required for d in demands.values())
	load.special_equipment_required = ", ".join(
		sorted(
			{
				s.strip()
				for d in demands.values()
				for s in (d.special_equipment_required or "").replace("\n", ",").split(",")
				if s.strip()
			}
		)
	)
	for row in load.delivery_orders:
		demand = demands[row.transport_demand]
		if (demand.transport_zone or None) != (
			load.transport_zone or None
		) or not delivery_methods_are_compatible(demand.delivery_method, load.delivery_method):
			frappe.throw(_("Customer demands must share a transport zone and compatible Delivery Method."))
		value = live[demand.name].get(row.transport_demand_item)
		if not value or row.transport_demand_item in seen:
			frappe.throw(_("Select each customer demand item once, from its owning demand."))
		seen.add(row.transport_demand_item)
		item = value["row"]
		qty = flt(row.allocated_quantity, row.precision("allocated_quantity"))
		if (
			not math.isfinite(qty)
			or qty <= 0
			or qty * item.conversion_factor > value["available_stock_qty"] + 1e-6
		):
			frappe.throw(
				_("Selected quantity exceeds the live unallocated quantity for item {0}.").format(
					item.item_code
				)
			)
		ratio = qty / flt(item.transaction_qty)
		row.allocated_quantity = qty
		row.item_code, row.item_name = item.item_code, item.item_name
		row.uom, row.stock_uom, row.conversion_factor = (
			item.transaction_uom,
			item.stock_uom,
			item.conversion_factor,
		)
		row.allocated_stock_qty = flt(qty * item.conversion_factor, row.precision("allocated_stock_qty"))
		row.stock_required_quantity = row.allocated_stock_qty
		row.stock_available_quantity = flt(bins.get(item.item_code))
		row.stock_availability_status = (
			("Available" if row.stock_available_quantity >= row.stock_required_quantity else "Insufficient")
			if load.source_warehouse
			else None
		)
		row.available_quantity = value["available_quantity"]
		row.customer, row.sales_order, row.delivery_address = (
			demand.customer,
			demand.sales_order,
			demand.destination_address,
		)
		row.destination_warehouse = None
		row.delivery_group, row.transport_compatibility_group = (
			item.delivery_group,
			item.transport_compatibility_group,
		)
		for dimension in ("weight", "volume", "pallets", "cases", "crates"):
			row.set(dimension, flt(item.get(dimension)) * ratio)
		row.weight_uom, row.volume_uom = item.weight_uom, item.volume_uom
		stops.setdefault((demand.customer, demand.destination_address), len(stops) + 1)
		row.stop_sequence = stops[(demand.customer, demand.destination_address)]
	validate_transport_compatibility_groups(list(demands.values()))
	for unit in ("weight_uom", "volume_uom"):
		units = {row.get(unit) for row in load.delivery_orders if row.get(unit)}
		if len(units) > 1:
			frappe.throw(_("Customer load items must use consistent transport Weight and Volume UOMs."))
		load.set(unit, next(iter(units), None))
	for dimension in ("weight", "volume", "pallets", "cases", "crates"):
		load.set("total_" + dimension, sum(flt(row.get(dimension)) for row in load.delivery_orders))
	load.total_stops = len(stops)
	load.minimum_payload = load.total_weight
	load.minimum_volume = load.total_volume
