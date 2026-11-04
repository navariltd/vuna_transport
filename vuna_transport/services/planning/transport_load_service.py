"""Requested-UOM allocation, reconciled internally in Stock UOM."""

import math

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from vuna_transport.services.demand.transport_demand_service import build_source_legs
from vuna_transport.services.planning.load_validation_service import validate_transport_compatibility_groups
from vuna_transport.services.shared.security_service import require_company_access, require_doctype_permission


def is_demand_load(load):
	return any(
		getattr(row, "transport_demand", None) and not getattr(row, "delivery_order", None)
		for row in load.delivery_orders
	)


def get_committed_quantities(item_names, exclude_load=None):
	if not item_names:
		return {}
	return dict(
		frappe.db.sql(
			"""select li.transport_demand_item, sum(coalesce(nullif(li.allocated_stock_qty, 0),
		li.allocated_quantity * di.conversion_factor))
		from `tabTransport Load Item` li join `tabTransport Load` l on l.name = li.parent
		join `tabTransport Demand Item` di on di.name = li.transport_demand_item
		where li.parenttype = 'Transport Load' and l.docstatus in (0, 1)
		and li.transport_demand_item in %(items)s and li.parent != %(exclude)s
		group by li.transport_demand_item""",
			{"items": tuple(item_names), "exclude": exclude_load or ""},
		)
	)


def validate_demand_allocations(load):
	"""Lock parent demands first so concurrent loads cannot over-reserve rows."""
	names = sorted(
		{row.get("transport_demand") for row in load.delivery_orders if row.get("transport_demand")}
	)
	if not names or any(not row.get("transport_demand") for row in load.delivery_orders):
		frappe.throw(_("Do not mix legacy Delivery Order rows and Transport Demand rows in one load."))
	frappe.db.sql(
		"select name from `tabTransport Demand` where name in %(names)s order by name for update",
		{"names": tuple(names)},
	)
	demands = {name: frappe.get_doc("Transport Demand", name) for name in names}
	committed = get_committed_quantities(
		[row.transport_demand_item for row in load.delivery_orders], load.name
	)
	seen = set()
	stops = set()
	for row in load.delivery_orders:
		demand = demands[row.transport_demand]
		demand.check_permission("read")
		require_company_access(demand.company)
		if demand.docstatus != 1 or demand.status in ("Cancelled", "Closed"):
			frappe.throw(_("Submit an open Transport Demand before allocating it."))
		if demand.company != load.company or demand.source_warehouse != load.source_warehouse:
			frappe.throw(_("Transport Load demands must share a company and loading warehouse."))
		# Phase 2 keeps warehouse transfers isolated from customer execution.
		if demand.movement_type != "Warehouse Transfer":
			frappe.throw(_("Customer deliveries currently use the Delivery Order planning path."))
		source = frappe.get_doc(demand.source_document_type, demand.source_document)
		source.check_permission("read")
		leg = next((leg for leg in build_source_legs(source) if leg["source_key"] == demand.source_key), None)
		item = next((item for item in demand.items if item.name == row.transport_demand_item), None)
		live = next(
			(
				value
				for value in (leg or {}).get("items", [])
				if item and value["source_item"] == item.source_item
			),
			None,
		)
		if not item or not live:
			frappe.throw(_("The selected demand row is no longer available from its source document."))
		quantity = flt(row.allocated_quantity)
		factor = flt(item.conversion_factor)
		if not math.isfinite(factor) or factor <= 0:
			frappe.throw(_("The requested UOM requires a positive conversion factor."))
		stock_quantity = flt(quantity * factor, row.precision("allocated_stock_qty"))
		available = max(min(flt(item.stock_qty), flt(live["stock_qty"])) - flt(committed.get(item.name)), 0)
		if (
			item.name in seen
			or not math.isfinite(quantity)
			or not math.isfinite(stock_quantity)
			or quantity <= 0
			or stock_quantity <= 0
			or flt(stock_quantity - available, row.precision("allocated_stock_qty")) > 0
		):
			frappe.throw(
				_("Demand item {0} is duplicated or exceeds its available quantity {1} {2}.").format(
					item.item_code, available / factor, item.transaction_uom
				)
			)
		seen.add(item.name)
		if (
			frappe.get_cached_value("UOM", item.stock_uom, "must_be_whole_number")
			and abs(stock_quantity - round(stock_quantity)) > 1e-9
		):
			frappe.throw(_("Stock UOM {0} requires a whole-number allocation.").format(item.stock_uom))
		if frappe.get_cached_value("UOM", item.transaction_uom, "must_be_whole_number") and quantity != int(
			quantity
		):
			frappe.throw(
				_("Requested UOM {0} requires a whole-number allocation.").format(item.transaction_uom)
			)
		for field in (
			"item_code",
			"stock_uom",
			"delivery_group",
			"transport_compatibility_group",
			"weight_uom",
		):
			row.set(field, item.get(field))
		row.item_name = frappe.get_cached_value("Item", item.item_code, "item_name")
		row.available_quantity = available / factor
		row.uom = item.transaction_uom
		row.conversion_factor = factor
		row.allocated_stock_qty = stock_quantity
		row.weight = flt(item.weight) * stock_quantity / flt(item.stock_qty)
		row.destination_warehouse = demand.destination_warehouse
		row.delivery_order = row.delivery_order_item = row.sales_order = row.customer = (
			row.delivery_address
		) = None
		stops.add(demand.destination_warehouse)
	validate_transport_compatibility_groups(load.delivery_orders)
	load.total_weight = sum(flt(row.weight) for row in load.delivery_orders)
	load.weight_uom = next(iter(demands.values())).items[0].weight_uom
	load.total_volume = load.total_pallets = load.total_cases = load.total_crates = 0
	load.total_stops = len(stops)
	load.minimum_payload = load.total_weight


@frappe.whitelist()
def create_transport_load_from_demands(allocations: list | str) -> str:
	"""Create a draft from quantities expressed in the requested transaction UOM."""
	require_doctype_permission("Transport Load", "create")
	rows = frappe.parse_json(allocations) if isinstance(allocations, str) else allocations
	if not isinstance(rows, list) or not rows or len(rows) > 500:
		frappe.throw(_("Select between 1 and 500 Transport Demand items."))
	first = frappe.get_doc("Transport Demand", rows[0].get("transport_demand"))
	first.check_permission("read")
	if not first.source_warehouse:
		frappe.throw(_("Select and submit the demand's Source Warehouse before creating a Transport Load."))
	load = frappe.new_doc("Transport Load")
	load.company = first.company
	load.source_warehouse = first.source_warehouse
	load.dispatch_date = first.required_date
	load.status = "Proposed"
	for row in rows:
		load.append(
			"delivery_orders",
			{
				key: row.get(key)
				for key in ("transport_demand", "transport_demand_item", "allocated_quantity")
			},
		)
	load.insert()
	return load.name


@frappe.whitelist()
def bundle_transport_demands(demand_names: list | str) -> str:
	"""Bundle outstanding items from planner-selected submitted demands."""
	require_doctype_permission("Transport Load", "create")
	names = frappe.parse_json(demand_names) if isinstance(demand_names, str) else demand_names
	if not isinstance(names, list) or not names or len(names) > 100:
		frappe.throw(_("Select between 1 and 100 Transport Demands."))
	allocations = []
	for name in sorted(set(names)):
		demand = frappe.get_doc("Transport Demand", name)
		demand.check_permission("read")
		require_company_access(demand.company)
		committed = get_committed_quantities([item.name for item in demand.items])
		for item in demand.items:
			quantity = (flt(item.stock_qty) - flt(committed.get(item.name))) / flt(item.conversion_factor)
			if quantity > 0:
				allocations.append(
					{
						"transport_demand": name,
						"transport_demand_item": item.name,
						"allocated_quantity": quantity,
					}
				)
	return create_transport_load_from_demands(allocations)


@frappe.whitelist()
def get_transfer_planning_items(
	company: str,
	source_warehouse: str | None = None,
	date_from: str | None = None,
	date_to: str | None = None,
	start: int = 0,
) -> dict:
	"""Bounded, permission-filtered transfer pool with live Stock UOM balances."""
	require_doctype_permission("Transport Demand", "read")
	require_company_access(company)
	if not company:
		frappe.throw(_("Select a company first."))
	if date_from and date_to and getdate(date_from) > getdate(date_to):
		frappe.throw(_("Required date range is invalid."))
	filters = [["company", "=", company], ["docstatus", "=", 1], ["movement_type", "=", "Warehouse Transfer"]]
	if source_warehouse:
		filters.append(["source_warehouse", "=", source_warehouse])
	if date_from:
		filters.append(["required_date", ">=", date_from])
	if date_to:
		filters.append(["required_date", "<=", date_to])
	demands = frappe.get_list(
		"Transport Demand",
		filters=filters,
		pluck="name",
		order_by="required_date asc, name asc",
		limit_start=max(cint(start), 0),
		limit_page_length=21,
	)
	rows = []
	for name in demands[:20]:
		demand = frappe.get_doc("Transport Demand", name)
		demand.check_permission("read")
		source = frappe.get_doc(demand.source_document_type, demand.source_document)
		source.check_permission("read")
		leg = next((leg for leg in build_source_legs(source) if leg["source_key"] == demand.source_key), None)
		live = {item["source_item"]: item["stock_qty"] for item in (leg or {}).get("items", [])}
		committed = get_committed_quantities([item.name for item in demand.items])
		for item in demand.items:
			available = max(
				min(flt(item.stock_qty), flt(live.get(item.source_item))) - flt(committed.get(item.name)), 0
			)
			if available <= 0:
				continue
			rows.append(
				{
					"transport_demand": name,
					"transport_demand_item": item.name,
					"item_code": item.item_code,
					"source_warehouse": demand.source_warehouse,
					"destination_warehouse": demand.destination_warehouse,
					"required_date": demand.required_date,
					"available_stock_qty": available,
					"stock_uom": item.stock_uom,
					"available_quantity": available / flt(item.conversion_factor),
					"uom": item.transaction_uom,
					"conversion_factor": item.conversion_factor,
					"weight": flt(item.weight) * available / flt(item.stock_qty),
					"weight_uom": item.weight_uom,
				}
			)
	return {"items": rows[:500], "has_more": len(demands) > 20, "truncated": len(rows) > 500}
