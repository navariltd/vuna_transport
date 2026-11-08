"""Derive the read-only item list from an allocation's selected loads."""

import frappe
from frappe import _
from frappe.utils import flt

from vuna_transport.services.shared.security_service import (
	require_doctype_permission,
	require_document_permission,
)


def collect_load_items(loads):
	"""Keep loads and UOMs separate; never combine unlike quantities or weights."""
	groups = {}
	sources = {}
	for load in loads:
		require_document_permission(load, "read")
		if load.docstatus == 2:
			frappe.throw(_("Remove cancelled Transport Loads from the allocation."))
		for row in load.delivery_orders:
			transfer = bool(row.get("transport_demand") and not row.delivery_order)
			key = (
				("Transport Demand", row.transport_demand)
				if transfer
				else ("Delivery Order", row.delivery_order)
			)
			if key not in sources:
				sources[key] = frappe.get_doc(*key)
				require_document_permission(sources[key], "read")
			item_name = row.transport_demand_item if transfer else row.delivery_order_item
			item = next((item for item in sources[key].items if item.name == item_name), None)
			if not item or item.item_code != row.item_code:
				frappe.throw(_("Item lineage is invalid in Transport Load {0}.").format(load.name))
			uom = item.transaction_uom if transfer else item.uom
			key = (load.name, row.item_code, uom, row.weight_uom)
			if key not in groups:
				groups[key] = {
					"transport_load": load.name,
					"item_code": row.item_code,
					"item_name": row.item_name,
					"qty": 0,
					"uom": uom,
					"weight": 0,
					"weight_uom": row.weight_uom,
				}
			groups[key]["qty"] += flt(row.allocated_quantity)
			groups[key]["weight"] += flt(row.weight)
	return list(groups.values())


@frappe.whitelist()
def get_allocation_items(load_names: list | str) -> list:
	require_doctype_permission("Vehicle Allocation", "read")
	names = frappe.parse_json(load_names) if isinstance(load_names, str) else load_names
	if not isinstance(names, list) or len(names) > 100 or any(not isinstance(name, str) for name in names):
		frappe.throw(_("Select no more than 100 Transport Loads."))
	return collect_load_items([frappe.get_doc("Transport Load", name) for name in dict.fromkeys(names)])
