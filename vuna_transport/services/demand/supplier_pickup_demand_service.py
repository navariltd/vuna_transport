"""Opted-in purchase demand only. No custody, stock, receipt or accounting posting."""

import math
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, getdate

from vuna_transport.services.demand.delivery_method_service import (
	get_purchase_delivery_method_workflow,
	is_company_pickup_order,
)


def get_supplier_pickup_address(order):
	return order.get("dispatch_address") or order.get("supplier_address")


def validate_supplier_pickup_order(doc, method=None):
	"""Existing demand snapshots cannot silently change their pickup identity."""
	previous = doc.get_doc_before_save()
	if not doc.get("vuna_delivery_method") and not (previous and previous.get("vuna_delivery_method")):
		doc.vuna_transport_arranged_by = None
		return
	fields = ("vuna_delivery_method", "supplier", "company", "set_warehouse")

	def endpoints(order):
		return sorted(
			(
				row.name,
				row.warehouse or order.get("set_warehouse") or "",
				str(row.schedule_date or order.get("schedule_date") or ""),
			)
			for row in order.get("items") or []
		)

	if previous and (
		any(previous.get(field) != doc.get(field) for field in fields)
		or get_supplier_pickup_address(previous) != get_supplier_pickup_address(doc)
		or endpoints(previous) != endpoints(doc)
	):
		# Use the same source lock as demand creation, so an address/opt-in
		# change cannot race creation of a demand with the previous identity.
		frappe.db.get_value("Purchase Order", doc.name, "name", for_update=True)
		if frappe.db.exists(
			"Transport Demand",
			{"source_document_type": "Purchase Order", "source_document": doc.name, "docstatus": ["!=", 2]},
		):
			frappe.throw(
				_(
					"Cancel linked Transport Demands before changing this Purchase Order's pickup configuration."
				)
			)
	workflow = get_purchase_delivery_method_workflow(doc.get("vuna_delivery_method"))
	if workflow.disabled or not workflow.allowed:
		frappe.throw(_("Select an enabled purchasing Delivery Method."))
	doc.vuna_transport_arranged_by = workflow.transport_arranged_by
	if not workflow.company_pickup:
		return
	validate_pickup_location(doc)


def validate_pickup_location(source):
	name = get_supplier_pickup_address(source)
	if not name:
		frappe.throw(_("Select a Dispatch Address or Supplier Address for company collection."))
	address = frappe.get_doc("Address", name)
	if address.disabled or not any(
		link.link_doctype == "Supplier" and link.link_name == source.supplier for link in address.links
	):
		frappe.throw(_("The pickup address must be active and linked to this Purchase Order's Supplier."))
	return address


def build_supplier_pickup_legs(source, *, preserved_source_items=()):
	"""ERPNext PO received_qty is transaction UOM, unlike MR ordered_qty.

	Use the same qty - received_qty arithmetic as make_purchase_receipt. Keep
	stock-UOM arithmetic alongside that snapshot, never substitute Stock UOM
	for the requested packaging UOM. External suppliers are not company warehouses.
	"""
	from vuna_transport.services.demand.item_transport_policy import item_requires_transport
	from vuna_transport.services.demand.transport_demand_service import get_source_key

	if (
		source.docstatus != 1
		or source.status in ("Closed", "Cancelled", "On Hold", "Stopped", "Completed")
		or not is_company_pickup_order(source)
	):
		frappe.throw(
			_(
				"Supplier transport requires an open, submitted Purchase Order with an enabled, company-arranged purchasing Delivery Method."
			)
		)
	if (
		source.get("is_subcontracted")
		or source.get("is_old_subcontracting_flow")
		or source.get("has_unit_price_items")
	):
		frappe.throw(
			_("Subcontracting and unit-price orders are not supported by the supplier pickup adapter.")
		)
	eligible_rows = [
		row
		for row in source.get("items") or []
		if not row.get("delivered_by_supplier")
		and (row.name in preserved_source_items or item_requires_transport(row.item_code))
	]
	if not eligible_rows:
		return []
	pickup_address = get_supplier_pickup_address(source)
	if not source.get("supplier") or not pickup_address:
		frappe.throw(
			_(
				"Select the Supplier and a Dispatch Address or Supplier Address before creating transport demand."
			)
		)
	validate_pickup_location(source)
	legs = defaultdict(list)
	for row in eligible_rows:
		if row.get("delivered_by_supplier"):
			continue  # Drop shipments never enter the company's receiving warehouse.
		if not frappe.get_cached_value("Item", row.item_code, "is_stock_item"):
			continue
		factor, ordered, received = flt(row.conversion_factor), flt(row.qty), flt(row.received_qty)
		if not all(math.isfinite(value) for value in (factor, ordered, received)) or factor <= 0:
			frappe.throw(
				_(
					"Purchase Order row {0} requires finite quantities and a positive conversion factor."
				).format(row.idx)
			)
		quantity = max(ordered - received, 0)
		if not quantity:
			continue
		stock_qty = quantity * factor
		if not math.isfinite(stock_qty):
			frappe.throw(_("Purchase Order row {0} has an invalid stock quantity.").format(row.idx))
		destination = row.warehouse or source.get("set_warehouse")
		if not destination:
			frappe.throw(_("Select the receiving Warehouse on Purchase Order row {0}.").format(row.idx))
		warehouse = frappe.get_doc("Warehouse", destination)
		if (
			warehouse.company != source.company
			or warehouse.disabled
			or warehouse.is_group
			or warehouse.warehouse_type == "Transit"
		):
			frappe.throw(
				_(
					"Supplier goods require an active, non-group receiving warehouse in the Purchase Order company, not vehicle transit."
				)
			)
		date = row.schedule_date or source.get("schedule_date")
		if not date:
			frappe.throw(_("Set the required date on Purchase Order row {0}.").format(row.idx))
		date = str(getdate(date))
		legs[(destination, date)].append(
			{
				"source_item": row.name,
				"item_code": row.item_code,
				"transaction_qty": quantity,
				"transaction_uom": row.uom,
				"conversion_factor": factor,
				"stock_qty": stock_qty,
				"stock_uom": row.stock_uom,
			}
		)
	return [
		{
			"company": source.company,
			"movement_type": "Supplier Pickup",
			"source_document_type": "Purchase Order",
			"source_document": source.name,
			"source_key": get_source_key("Purchase Order", source.name, pickup_address, [destination, date]),
			"source_warehouse": None,
			"source_address": pickup_address,
			"supplier": source.supplier,
			"customer": None,
			"destination_address": None,
			"destination_warehouse": destination,
			"required_date": date,
			"items": items,
		}
		for (destination, date), items in sorted(legs.items())
	]
