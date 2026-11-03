"""Explicit, permission-checked transport demand generation. No stock posting."""

import hashlib
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

from vuna_transport.services.demand.delivery_order_service import _get_item_transport_measurements
from vuna_transport.services.shared.security_service import (
	require_company_access,
	require_doctype_permission,
)

SUPPORTED_SOURCES = {"Delivery Order", "Material Request"}


def build_source_legs(source):
	"""Normalize source rows; never sum quantities across different stock UOMs."""
	if source.doctype not in SUPPORTED_SOURCES or source.docstatus != 1:
		frappe.throw(_("Transport demand requires a supported, submitted source document."))
	if source.status in ("Stopped", "Closed", "Cancelled"):
		frappe.throw(_("The source document is no longer available for transport planning."))
	is_transfer = source.doctype == "Material Request"
	if is_transfer and source.material_request_type != "Material Transfer":
		frappe.throw(_("Only Material Transfer requests can generate transport demand."))
	legs = defaultdict(list)
	for row in source.get("items"):
		factor = flt(row.conversion_factor)
		if factor <= 0:
			frappe.throw(_("Source row {0} requires a positive UOM conversion factor.").format(row.idx))
		if is_transfer:
			origin = row.from_warehouse or source.get("set_from_warehouse")
			destination = row.warehouse or source.get("set_warehouse")
			# ERPNext tracks Material Request fulfilment in stock UOM as ordered_qty.
			quantity = max(flt(row.stock_qty) - flt(row.ordered_qty), 0)
		else:
			origin = row.s_warehouse or source.source_warehouse
			destination = source.delivery_address
			quantity = flt(row.transport_qty) * factor
		if quantity <= 0:
			continue
		if not origin or not destination or (is_transfer and origin == destination):
			frappe.throw(_("Source row {0} requires distinct, valid movement endpoints.").format(row.idx))
		legs[(origin, destination)].append(
			{
				"source_item": row.name,
				"item_code": row.item_code,
				"transaction_qty": quantity / factor,
				"transaction_uom": row.uom,
				"conversion_factor": factor,
				"stock_qty": quantity,
				"stock_uom": row.stock_uom,
				"delivery_group": row.get("delivery_group"),
				"transport_compatibility_group": row.get("transport_compatibility_group"),
			}
		)
	return [
		{
			"company": source.company,
			"movement_type": "Warehouse Transfer" if is_transfer else "Customer Delivery",
			"source_document_type": source.doctype,
			"source_document": source.name,
			"source_key": get_source_key(source.doctype, source.name, origin, destination),
			"source_warehouse": origin,
			"destination_warehouse": destination if is_transfer else None,
			"destination_address": None if is_transfer else destination,
			"customer": None if is_transfer else source.customer,
			"required_date": source.schedule_date if is_transfer else source.required_delivery_date,
			"transport_zone": None if is_transfer else source.transport_zone,
			"items": items,
		}
		for (origin, destination), items in sorted(legs.items())
	]


def get_source_key(doctype, name, origin, destination):
	return hashlib.sha256(frappe.as_json([doctype, name, origin, destination]).encode()).hexdigest()


@frappe.whitelist()
def create_transport_demands(source_doctype: str, source_name: str) -> list[str]:
	"""Create reviewable demand drafts, returning existing drafts on retry."""
	require_doctype_permission("Transport Demand", "create")
	return _create_transport_demands(source_doctype, source_name)


def _create_transport_demands(source_doctype, source_name, automated=False):
	from vuna_transport.services.demand.source_policy_service import require_source_enabled

	if source_doctype not in SUPPORTED_SOURCES:
		frappe.throw(_("Unsupported transport demand source."))
	source = frappe.get_doc(source_doctype, source_name)
	if not automated:
		source.check_permission("read")
	require_company_access(source.company)
	policy = require_source_enabled(source)
	# Serialize generation for this source; source_key also has a unique constraint.
	frappe.qb.from_(frappe.qb.DocType(source_doctype)).select("name").where(
		frappe.qb.DocType(source_doctype).name == source_name
	).for_update().run()
	source.reload()
	legs = build_source_legs(source)
	if not legs:
		frappe.throw(_("The source has no outstanding transport quantity."))
	names = []
	for leg in legs:
		existing = frappe.db.get_value("Transport Demand", {"source_key": leg["source_key"]}, "name")
		if existing:
			demand = frappe.get_doc("Transport Demand", existing)
			if not automated:
				demand.check_permission("read")
			if demand.docstatus == 2:
				frappe.throw(
					_("Cancelled Transport Demand {0} requires planner review before regeneration.").format(
						existing
					)
				)
			names.append(existing)
			continue
		demand = frappe.get_doc({"doctype": "Transport Demand", **leg})
		demand.flags.ignore_permissions = automated
		demand.insert()
		if policy.auto_submit_demand:
			demand.submit()
		names.append(demand.name)
	return names


def validate_transport_demand(demand):
	"""Rebuild draft snapshots from the source, preventing forged rows/endpoints."""
	from vuna_transport.services.demand.source_policy_service import require_source_enabled

	if demand.source_document_type not in SUPPORTED_SOURCES:
		frappe.throw(_("Unsupported transport demand source."))
	source = frappe.get_doc(demand.source_document_type, demand.source_document)
	require_source_enabled(source)
	if not demand.flags.ignore_permissions:
		source.check_permission("read")
	require_company_access(source.company)
	previous = demand.get_doc_before_save()
	if previous and previous.source_key != demand.source_key:
		frappe.throw(_("Transport demand source lineage cannot be changed."))
	leg = next((leg for leg in build_source_legs(source) if leg["source_key"] == demand.source_key), None)
	if not leg:
		frappe.throw(_("This movement leg no longer has outstanding source quantity."))
	for warehouse_name in (leg["source_warehouse"], leg["destination_warehouse"]):
		if not warehouse_name:
			continue
		warehouse = frappe.get_doc("Warehouse", warehouse_name)
		if not demand.flags.ignore_permissions:
			warehouse.check_permission("read")
		if warehouse.company != leg["company"] or warehouse.is_group or warehouse.disabled:
			frappe.throw(_("Movement warehouses must be active, non-group warehouses in the source company."))
	for fieldname, value in leg.items():
		if fieldname != "items":
			demand.set(fieldname, value)
	demand.set("items", leg["items"])
	for row in demand.items:
		item = frappe.get_cached_doc("Item", row.item_code)
		if not item.is_stock_item or item.disabled:
			frappe.throw(_("Transport demand items must be active stock items."))
		row.delivery_group = row.delivery_group or item.get("vuna_delivery_group")
		row.transport_compatibility_group = row.transport_compatibility_group or item.get(
			"vuna_transport_compatibility_group"
		)
		measurements = _get_item_transport_measurements(row, row.transaction_qty)
		row.weight = measurements.weight
		row.weight_uom = measurements.weight_uom
	demand.status = "Open" if demand.docstatus == 1 else "Draft"
