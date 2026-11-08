"""Direct Sales Order -> customer Transport Demand, without a shadow Delivery Order."""

import hashlib
import math
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

from vuna_transport.services.demand import delivery_order_service as sales
from vuna_transport.services.demand.customer_demand_fields import ITEM_FIELDS, ROOT_FIELDS, customer_fields
from vuna_transport.services.routing.location_service import (
	get_address_planning_data,
	get_warehouse_planning_data,
)
from vuna_transport.services.shared.security_service import require_company_access, require_doctype_permission

INACTIVE_SALES_STATUSES = ("Closed", "Cancelled", "On Hold", "Stopped", "Completed")


def native_customer_demand_enabled():
	"""Temporary site opt-in while the legacy execution path is still in service."""
	from frappe.utils import cint

	return not frappe.db.exists("DocType", "Delivery Order") or bool(
		cint(frappe.conf.get("customer_demand_cutover"))
	)


def sales_source_key(source_name, group_key, item_names):
	return hashlib.sha256(
		frappe.as_json(["Sales Order", source_name, group_key, sorted(item_names)]).encode()
	).hexdigest()


def _lock_sales_order(name):
	frappe.db.get_value("Sales Order", name, "name", for_update=True)


def covered_source_items(sales_order, exclude=None):
	"""Do not duplicate active native demand or unmigrated legacy sales planning."""
	covered = set(
		frappe.db.sql_list(
			"""select i.source_item from `tabTransport Demand Item` i
		join `tabTransport Demand` d on d.name=i.parent
		where d.source_document_type='Sales Order' and d.source_document=%s
		and d.docstatus!=2 and coalesce(d.migration_archived,0)=0 and d.name!=%s and i.parenttype='Transport Demand'""",
			(sales_order, exclude or ""),
		)
	)
	legacy = (
		frappe.db.sql_list(
			"""select i.sales_order_item from `tabDelivery Order Item` i
		join `tabDelivery Order` d on d.name=i.parent
		where d.sales_order=%s and d.docstatus!=2 and i.parenttype='Delivery Order'""",
			sales_order,
		)
		if frappe.db.exists("DocType", "Delivery Order")
		else []
	)
	if any(not name for name in legacy):
		frappe.throw(
			_(
				"An existing Delivery Order has unresolved Sales Order row references; review it before generating demand."
			)
		)
	covered.update(legacy)
	return covered


def build_sales_legs(source, selected=None):
	"""Reuse established grouping, handling and measurement rules; canonical names only."""
	if source.docstatus != 1 or source.status in INACTIVE_SALES_STATUSES:
		frappe.throw(_("Select an open, submitted Sales Order for customer transport demand."))
	if not sales._requires_transport_planning(source):
		return []
	groups = defaultdict(list)
	for row in source.items:
		if selected is not None and row.name not in selected:
			continue
		if not sales._is_stock_item(row):
			continue
		quantity = sales._get_transport_quantity(row)
		factor = flt(row.conversion_factor)
		if not math.isfinite(quantity) or not math.isfinite(factor) or factor <= 0:
			frappe.throw(
				_("Customer demand requires finite quantities and a positive source UOM conversion factor.")
			)
		if quantity <= 0:
			continue
		key = sales._get_group_key(source, row)
		if key[1]:
			groups[key].append((row, quantity))
	legs = []
	for key, rows in groups.items():
		snapshot = sales.build_sales_planning_snapshot(source, key, rows)
		address = get_address_planning_data(key[1])
		origin = get_warehouse_planning_data(key[2])
		leg = {
			**customer_fields(snapshot, ROOT_FIELDS),
			"company": source.company,
			"movement_type": "Customer Delivery",
			"demand_type": "Selling",
			"source_document_type": "Sales Order",
			"source_document": source.name,
			"source_key": sales_source_key(source.name, key, [r.name for r, _qty in rows]),
			"source_warehouse": key[2],
			"destination_address": key[1],
			"destination_warehouse": None,
			"customer": source.customer,
			"required_date": key[0],
			"transport_zone": key[3],
			"source_address": origin.address,
			"source_location": origin.location,
			"source_latitude": origin.latitude,
			"source_longitude": origin.longitude,
			"destination_location": address.location,
			"destination_latitude": address.latitude,
			"destination_longitude": address.longitude,
			"items": [],
		}
		for row in snapshot["items"]:
			leg["items"].append(
				{
					**customer_fields(row, ITEM_FIELDS),
					"source_item": row["sales_order_item"],
					"item_code": row["item_code"],
					"transaction_qty": row["transport_qty"],
					"transaction_uom": row["uom"],
					"conversion_factor": row["conversion_factor"],
					"stock_qty": row["stock_qty"],
					"stock_uom": row["stock_uom"],
					"delivery_group": row.get("delivery_group"),
					"transport_compatibility_group": row.get("transport_compatibility_group"),
					"weight": row.get("weight"),
					"weight_uom": row.get("weight_uom"),
				}
			)
		legs.append(leg)
	return legs


def create_sales_demands(source, automated=False):
	from vuna_transport.services.demand.source_policy_service import require_source_enabled

	source = frappe.get_doc("Sales Order", source if isinstance(source, str) else source.name)
	if not automated:
		source.check_permission("read")
		require_doctype_permission("Transport Demand", "create")
	require_company_access(source.company)
	policy = require_source_enabled(source)
	_lock_sales_order(source.name)
	source.reload()
	covered = covered_source_items(source.name)
	selected = {r.name for r in source.items if r.name not in covered}
	names = []
	for leg in build_sales_legs(source, selected):
		old = frappe.db.get_value(
			"Transport Demand", {"source_key": leg["source_key"]}, ["name", "docstatus"], as_dict=True
		)
		if old:
			if old.docstatus == 2:
				frappe.throw(
					_("Cancelled demand {0} requires planner review before regeneration.").format(old.name)
				)
			names.append(old.name)
			continue
		demand = frappe.get_doc({"doctype": "Transport Demand", **leg})
		demand.insert(ignore_permissions=automated)
		if policy.auto_submit_demand:
			demand.flags.ignore_permissions = automated
			demand.submit()
		names.append(demand.name)
	# Return the complete native set on first generation, later added-row batches
	# and retry, without changing existing demand identities.
	names = frappe.get_all(
		"Transport Demand",
		filters={
			"source_document_type": "Sales Order",
			"source_document": source.name,
			"docstatus": ["!=", 2],
		},
		pluck="name",
		order_by="name",
	)
	if not automated:
		for name in names:
			frappe.get_doc("Transport Demand", name).check_permission("read")
	return names


def refresh_sales_demands(source):
	"""Refresh source-derived quantities, preserving rows and committed stock.

	These are planning snapshots only. Source postings remain ERPNext's job.
	"""
	from vuna_transport.services.planning.customer_demand_load_service import posted_sales_quantities
	from vuna_transport.services.planning.demand_lifecycle_service import sync_demand_allocations
	from vuna_transport.services.planning.transport_load_service import get_committed_quantities

	_lock_sales_order(source.name)
	by_source = {row.name: row for row in source.items}
	for name in frappe.get_all(
		"Transport Demand",
		filters={
			"source_document_type": "Sales Order",
			"source_document": source.name,
			"docstatus": ["!=", 2],
		},
		pluck="name",
	):
		demand = frappe.get_doc("Transport Demand", name)
		if demand.get("migration_archived"):
			continue
		if demand.company != source.company:
			frappe.throw(_("Customer demand company does not match its source Sales Order."))
		if source.status in ("Closed", "Cancelled"):
			if demand.docstatus == 1 and demand.status not in ("Delivered", "Cancelled"):
				frappe.db.set_value("Transport Demand", name, "status", "Closed", update_modified=False)
			continue
		committed = get_committed_quantities([row.name for row in demand.items])
		posted = posted_sales_quantities([row.name for row in demand.items])
		keys = set()
		updates = []
		for row in demand.items:
			original = by_source.get(row.source_item)
			if (
				not original
				or original.item_code != row.item_code
				or original.uom != row.transaction_uom
				or original.stock_uom != row.stock_uom
				or abs(flt(original.conversion_factor) - flt(row.conversion_factor)) > 1e-9
			):
				frappe.throw(
					_("Release customer demand {0} before removing or changing its source item/UOM.").format(
						name
					)
				)
			keys.add(sales._get_group_key(source, original))
			factor = flt(row.conversion_factor)
			live_stock = sales._get_transport_quantity(original) * factor
			pending = max(flt(committed.get(row.name)) - flt(posted.get(row.name)), 0)
			if live_stock + 1e-6 < pending:
				frappe.throw(
					_(
						"Release reserved Transport Load quantities for {0} before reducing the Sales Order quantity."
					).format(row.item_code)
				)
			quantity = (live_stock + flt(posted.get(row.name))) / factor
			values = sales._get_delivery_order_item_values(
				original, quantity, demand.transport_compatibility_group, demand.delivery_group
			)
			values = {
				**customer_fields(values, ITEM_FIELDS),
				**{
					key: values.get(key)
					for key in ("weight", "weight_uom", "volume", "volume_uom", "pallets", "cases", "crates")
				},
				"transaction_qty": quantity,
				"stock_qty": quantity * factor,
			}
			updates.append((row, values))
		if (
			len(keys) != 1
			or sales_source_key(source.name, next(iter(keys)), [row.source_item for row in demand.items])
			!= demand.source_key
		):
			frappe.throw(_("Release customer demand {0} before changing its delivery grouping.").format(name))
		for row, values in updates:
			frappe.db.set_value("Transport Demand Item", row.name, values, update_modified=False)
			row.update(values)
		frappe.db.set_value(
			"Transport Demand",
			name,
			{
				**{
					unit: next((row.get(unit) for row in demand.items if row.get(unit)), None)
					for unit in ("weight_uom", "volume_uom")
				},
				**{
					"total_" + dim: sum(flt(row.get(dim)) for row in demand.items)
					for dim in ("weight", "volume", "pallets", "cases", "crates")
				},
			},
			update_modified=False,
		)
		sync_demand_allocations([name])


def validate_sales_demand(demand):
	from vuna_transport.services.demand.source_policy_service import require_source_enabled
	from vuna_transport.services.demand.transport_demand_service import preserve_source_row_names

	_lock_sales_order(demand.source_document)
	source = frappe.get_doc("Sales Order", demand.source_document)
	if not demand.flags.ignore_permissions:
		source.check_permission("read")
	require_company_access(source.company)
	require_source_enabled(source)
	previous = demand.get_doc_before_save()
	if (previous and previous.get("migration_archived")) or demand.get("migration_archived"):
		frappe.throw(_("Historical migration demands are read-only and cannot create new transport work."))
	selected = {r.source_item for r in demand.items}
	if len(selected) != len(demand.items):
		frappe.throw(_("Select each Sales Order row only once in customer demand."))
	if previous:
		if (
			previous.source_document_type != "Sales Order"
			or previous.source_document != source.name
			or previous.source_key != demand.source_key
		):
			frappe.throw(_("Customer transport demand source lineage cannot be changed."))
		if selected != {r.source_item for r in previous.items}:
			frappe.throw(
				_("Customer source rows cannot be replaced; generate demand for additional Sales Order rows.")
			)
	if selected & covered_source_items(source.name, demand.name):
		frappe.throw(_("One or more Sales Order rows already have active transport planning."))
	legs = build_sales_legs(source, selected)
	if (
		len(legs) != 1
		or legs[0]["source_key"] != demand.source_key
		or selected != {r["source_item"] for r in legs[0]["items"]}
	):
		frappe.throw(_("Selected customer rows must form one valid, unchanged delivery group."))
	leg = legs[0]
	if not demand.flags.ignore_permissions:
		frappe.get_doc("Address", leg["destination_address"]).check_permission("read")
	# Demand may leave fulfilment origin unresolved; the load resolves it later.
	for key, value in leg.items():
		if key != "items":
			demand.set(key, value)
	demand.set("items", preserve_source_row_names(leg["items"], previous))
	if demand.source_warehouse:
		warehouse = frappe.get_doc("Warehouse", demand.source_warehouse)
		if not demand.flags.ignore_permissions:
			warehouse.check_permission("read")
		if warehouse.company != demand.company or warehouse.is_group or warehouse.disabled:
			frappe.throw(_("Select an active loading warehouse in the demand company."))
	demand.status = "Open" if demand.docstatus == 1 else "Draft"


@frappe.whitelist()
def get_generation_status(sales_order: str) -> dict:
	from vuna_transport.services.demand.source_policy_service import get_source_policy

	if not native_customer_demand_enabled():
		return {**sales.get_delivery_order_generation_status(sales_order), "native_demand": False}

	source = frappe.get_doc("Sales Order", sales_order)
	source.check_permission("read")
	require_company_access(source.company)
	policy = get_source_policy("Sales Order")
	if not policy.enabled or source.docstatus != 1 or source.status in INACTIVE_SALES_STATUSES:
		return {"can_generate": False, "native_demand": True}
	if policy.required_workflow_state and source.get("workflow_state") != policy.required_workflow_state:
		return {"can_generate": False, "native_demand": True}
	covered = covered_source_items(source.name)
	return {
		"native_demand": True,
		"can_generate": bool(
			build_sales_legs(source, {r.name for r in source.items if r.name not in covered})
		),
	}


@frappe.whitelist()
def generate(sales_order: str) -> list[str]:
	if not native_customer_demand_enabled():
		return sales.generate_delivery_orders_for_sales_order(sales_order)
	return create_sales_demands(sales_order)
