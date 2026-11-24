"""Repair only provable billing-first snapshots with no Transport Load history."""

import frappe

from vuna_transport.services.demand import delivery_order_service as grouping
from vuna_transport.services.demand import sales_transport_demand_service as sales
from vuna_transport.services.planning.customer_demand_load_service import (
	CustomerDemandGroupingChanged,
	available_items,
)

ADDRESS_FIELDS = (
	"destination_address",
	"destination_location",
	"destination_latitude",
	"destination_longitude",
	"transport_zone",
	"territory",
	"county",
	"constituency",
	"town",
	"delivery_from_time",
	"delivery_to_time",
	"average_service_duration",
	"maximum_vehicle_category",
	"restricted_access",
	"loading_dock_available",
	"access_notes",
	"tail_lift_required",
)


def repair_values(demand, source):
	"""Return a corrected address snapshot only when the old hash proves its origin."""
	if not source.shipping_address_name or not source.customer_address:
		return None
	selected = {row.source_item for row in demand.items}
	if len(selected) != len(demand.items):
		return None
	originals = [row for row in source.items if row.name in selected]
	if len(originals) != len(selected):
		return None
	# Reproduce the old billing-before-shipping rule without changing the source.
	legacy = frappe._dict(source.as_dict())
	legacy.shipping_address_name = None
	keys = {grouping._get_group_key(legacy, row) for row in originals}
	if len(keys) != 1:
		return None
	key = next(iter(keys))
	if (
		demand.destination_address != key[1]
		or sales.sales_source_key(source.name, key, selected) != demand.source_key
	):
		return None
	legs = sales.build_sales_legs(source, selected, preserved_source_items=selected)
	if len(legs) != 1 or {row["source_item"] for row in legs[0]["items"]} != selected:
		return None
	leg = legs[0]
	if leg["source_key"] == demand.source_key:
		return None
	return {"source_key": leg["source_key"], **{field: leg.get(field) for field in ADDRESS_FIELDS}}


def repair_shipping_address_snapshots(dry_run=True):
	"""Migration/admin operation; never rewrites a demand used by any load.

	Default is read-only. Writers must be paused for the migration, as with the
	other source-lineage upgrades. Quantity, child names and execution stay intact.
	"""
	if not dry_run:
		frappe.only_for("System Manager")
	report = {"repairable": [], "repaired": [], "needs_review": []}
	for name in frappe.get_all(
		"Transport Demand",
		filters={
			"source_document_type": "Sales Order",
			"movement_type": "Customer Delivery",
			"docstatus": 1,
			"migration_archived": 0,
			"status": ["not in", ["Closed", "Cancelled", "Delivered"]],
		},
		pluck="name",
	):
		demand = frappe.get_doc("Transport Demand", name, for_update=not dry_run)
		source = frappe.get_doc("Sales Order", demand.source_document, for_update=not dry_run)
		if source.docstatus != 1 or source.status in sales.INACTIVE_SALES_STATUSES:
			continue
		mute = frappe.flags.mute_messages
		try:
			frappe.flags.mute_messages = True
			available_items(demand)
		except CustomerDemandGroupingChanged:
			pass
		except frappe.ValidationError:
			report["needs_review"].append(name)
			continue
		else:
			continue
		finally:
			frappe.flags.mute_messages = mute
		if frappe.db.exists("Transport Load Item", {"transport_demand": name}):
			report["needs_review"].append(name)
			continue
		values = repair_values(demand, source)
		if not values:
			report["needs_review"].append(name)
			continue
		# Keep the unique source-lineage constraint intact, including cancelled rows.
		if frappe.db.exists("Transport Demand", {"source_key": values["source_key"], "name": ["!=", name]}):
			report["needs_review"].append(name)
			continue
		report["repairable"].append(name)
		if not dry_run:
			frappe.db.set_value("Transport Demand", name, values)
			report["repaired"].append(name)
	return report
