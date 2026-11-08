"""Quantity-based depot receipt of rejected goods; never changes customer PODs."""

import math

import frappe
from frappe import _
from frappe.utils import flt

WITH_DRIVER = "All remain on vehicle"
WITH_DEPOT = "All received by depot"
SPLIT = "Split between depot and vehicle"


def normalize_handling(row, submitted=True):
	"""The choice controls quantities; only split requires a quantity input.

	Infer missing choices for pre-existing PODs and older mobile clients without
	changing their recorded physical quantities.
	"""
	rejected = flt(row.rejected_qty)
	if not rejected:
		row.rejected_goods_handling = None
		row.depot_received_rejected_qty = 0
		row.depot_received_rejected_serial_no = ""
		return 0
	qty = depot_rejected_qty(row)
	handling = row.get("rejected_goods_handling")
	if not handling:
		handling = WITH_DRIVER if qty == 0 else WITH_DEPOT if qty == rejected else SPLIT
	if handling not in (WITH_DRIVER, WITH_DEPOT, SPLIT):
		frappe.throw(_("Choose where the rejected goods are: vehicle, depot, or split."))
	if handling == WITH_DRIVER:
		qty = 0
		row.depot_received_rejected_serial_no = ""
	elif handling == WITH_DEPOT:
		qty = rejected
		row.depot_received_rejected_serial_no = row.rejected_serial_no or ""
	elif submitted and not (0 < qty < rejected):
		frappe.throw(
			_(
				"For Split, enter a depot quantity greater than zero and less than the rejected quantity. Otherwise choose all vehicle or all depot."
			)
		)
	row.rejected_goods_handling = handling if rejected else None
	row.depot_received_rejected_qty = qty
	return qty


def depot_rejected_qty(row):
	return flt(row.get("depot_received_rejected_qty"))


def receipt_groups(proof):
	"""Separate accepted goods from rejected goods physically unloaded at depot."""
	groups = []
	for row in proof.items:
		for kind, qty, serials in (
			("Accepted", flt(row.delivered_qty), row.serial_no),
			("Rejected", depot_rejected_qty(row), row.get("depot_received_rejected_serial_no")),
		):
			if qty > 0:
				groups.append(frappe._dict(result=row, kind=kind, qty=qty, serial_no=serials or ""))
	return groups


def rejection_custody_groups(proof, transit_warehouse):
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

	groups = {}
	for row in proof.items:
		depot = depot_rejected_qty(row)
		depot_serials = get_serial_nos(row.get("depot_received_rejected_serial_no") or "")
		driver_serials = [
			serial
			for serial in get_serial_nos(row.rejected_serial_no or "")
			if serial not in set(depot_serials)
		]
		for warehouse, qty, serials in (
			(proof.get("rejected_goods_warehouse"), depot, depot_serials),
			(transit_warehouse, max(flt(row.rejected_qty) - depot, 0), driver_serials),
		):
			if qty > 0:
				groups[(row.name, warehouse)] = frappe._dict(
					result=row, qty=qty, serial_no="\n".join(serials), warehouse=warehouse
				)
	return groups


def validate_depot_custody(proof, stock, destination):
	from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos

	precision = frappe.get_precision("Stock Entry Detail", "transfer_qty")
	depot_qty = 0
	for row in proof.items:
		qty = normalize_handling(row, submitted=getattr(proof, "docstatus", 1) == 1)
		factor = flt(row.conversion_factor)
		if not math.isfinite(qty) or qty < 0 or flt((qty - flt(row.rejected_qty)) * factor, precision) > 0:
			frappe.throw(_("Rejected quantity received by depot must be between zero and rejected quantity."))
		for uom, value in ((row.uom, qty), (row.stock_uom, qty * factor)):
			if (
				frappe.get_cached_value("UOM", uom, "must_be_whole_number")
				and abs(value - round(value)) > 1e-9
			):
				frappe.throw(_("UOM {0} requires whole quantities.").format(uom))
		serials = get_serial_nos(row.get("depot_received_rejected_serial_no") or "")
		rejected = get_serial_nos(row.rejected_serial_no or "")
		if (
			len(serials) != len(set(serials))
			or not set(serials).issubset(rejected)
			or (rejected and len(serials) != flt(qty * factor, precision))
			or (not qty and serials)
		):
			frappe.throw(_("Select the rejected serial numbers actually received by the depot."))
		row.rejected_with_driver_qty = max(flt(row.rejected_qty) - qty, 0)
		depot_qty += qty
	proof.rejected_goods_warehouse = None
	if not depot_qty:
		return
	configured_warehouse = frappe.db.get_value("Warehouse", destination, "vuna_returns_warehouse")
	# A separate holding warehouse is optional. Preserve the rejected outcome
	# and custody on the POD/RR even when both groups enter the depot's stock.
	warehouse = configured_warehouse or destination
	config = frappe.get_doc("Warehouse", warehouse)
	if config.get("is_rejected_warehouse") and "nl_vms" in frappe.get_installed_apps():
		frappe.throw(
			_(
				"Use a holding/quarantine warehouse without Rejected Warehouse enabled: this site's nl_vms app automatically writes off stock received into rejected warehouses."
			)
		)
	if (
		config.company != proof.company
		or config.disabled
		or config.is_group
		or warehouse == stock.to_warehouse
		or (configured_warehouse and warehouse == destination)
		or config.warehouse_type == "Transit"
	):
		frappe.throw(
			_(
				"Receive rejected goods into an active same-company storage warehouse, not vehicle transit. A configured Returns / Quarantine Warehouse must be distinct from the receiving depot."
			)
		)
	proof.rejected_goods_warehouse = warehouse
