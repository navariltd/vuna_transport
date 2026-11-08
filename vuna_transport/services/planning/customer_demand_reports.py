"""Permission-aware, requested-UOM customer demand reporting."""

import frappe
from frappe import _

from vuna_transport.services.planning.customer_planning_source import metrics
from vuna_transport.services.shared.security_service import require_company_access, require_doctype_permission


def execute(filters=None):
	filters = frappe._dict(filters or {})
	require_doctype_permission("Transport Demand", "read")
	conditions = {
		"migration_archived": 0,
		"docstatus": 1,
		"movement_type": "Customer Delivery",
		"source_document_type": "Sales Order",
		"status": ["not in", ["Closed", "Cancelled", "Delivered"]],
	}
	for key in ("company", "source_warehouse", "transport_zone", "delivery_method", "priority", "customer"):
		if filters.get(key):
			conditions[key] = filters[key]
	from vuna_transport.services.planning.dispatch_planning_service import _delivery_date_filter

	date_range = _delivery_date_filter(filters.from_date, filters.to_date)
	if date_range:
		conditions["required_date"] = date_range
	from vuna_transport.services.demand.sales_transport_demand_service import INACTIVE_SALES_STATUSES

	conditions["sales_order"] = [
		"in",
		frappe.get_all(
			"Sales Order",
			filters={"docstatus": 1, "status": ["not in", INACTIVE_SALES_STATUSES]},
			pluck="name",
		),
	]
	parents = frappe.get_list(
		"Transport Demand",
		filters=conditions,
		fields=[
			"name",
			"company",
			"customer",
			"source_warehouse",
			"required_date",
			"transport_zone",
			"priority",
			"status",
		],
		order_by="required_date asc, creation asc",
		limit_page_length=500,
	)
	for company in {row.company for row in parents}:
		require_company_access(company)
	balances = metrics([row.name for row in parents], include_items=True)
	rows = []
	for parent in parents:
		for item in balances[parent.name]["items"]:
			if item["available_quantity"] <= 0:
				continue
			rows.append(
				{
					**dict(parent),
					"item_code": item["item_code"],
					"uom": item["uom"],
					"requested_quantity": item["transport_quantity"],
					"reserved_quantity": item["reserved_quantity"],
					"allocated_quantity": item["allocated_quantity"],
					"unallocated_quantity": item["available_quantity"],
					"weight": item["weight"],
					"volume": item["volume"],
					"weight_uom": item["weight_uom"],
					"volume_uom": item["volume_uom"],
				}
			)
	columns = [
		{
			"label": _("Transport Demand"),
			"fieldname": "name",
			"fieldtype": "Link",
			"options": "Transport Demand",
			"width": 190,
		},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company"},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer"},
		{
			"label": _("Warehouse"),
			"fieldname": "source_warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
		},
		{"label": _("Required Date"), "fieldname": "required_date", "fieldtype": "Date"},
		{"label": _("Zone"), "fieldname": "transport_zone", "fieldtype": "Link", "options": "Transport Zone"},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item"},
		{"label": _("UOM"), "fieldname": "uom", "fieldtype": "Link", "options": "UOM"},
	]
	columns += [
		{"label": _(label), "fieldname": key, "fieldtype": "Float"}
		for key, label in (
			("requested_quantity", "Requested"),
			("reserved_quantity", "Reserved"),
			("allocated_quantity", "Allocated"),
			("unallocated_quantity", "Remaining"),
			("weight", "Weight"),
			("volume", "Volume"),
		)
	]
	columns.append({"label": _("Status"), "fieldname": "status", "fieldtype": "Data"})
	return columns, rows
