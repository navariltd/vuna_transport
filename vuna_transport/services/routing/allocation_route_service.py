"""Approved allocation routes and retained, verified road-distance estimates."""

import hashlib
import json

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime

from vuna_transport.services.fleet.vehicle_allocation_service import get_allocation_loads
from vuna_transport.services.routing.location_service import (
	get_address_planning_data,
	get_warehouse_planning_data,
)
from vuna_transport.services.routing.routing_policy_service import require_routing_role
from vuna_transport.services.shared.security_service import require_document_permission


def fingerprint(value):
	return hashlib.sha256(
		json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
	).hexdigest()


def build_manifest(allocation, loads=None, *, return_to_start=None):
	loads = loads if loads is not None else get_allocation_loads(allocation)
	if not loads:
		frappe.throw(_("Select Transport Loads before building the allocation route."))
	origins = {load.source_warehouse for load in loads}
	if len(origins) != 1 or not next(iter(origins)):
		frappe.throw(
			_(
				"Allocation routing currently requires one common Source Warehouse. Multiple loading locations need an explicit pickup plan."
			)
		)
	groups, source_rows, locations = {}, [], {}
	for load in loads:
		require_document_permission(load, "read")
		if (
			load.company != allocation.company
			or load.docstatus != 1
			or load.movement_type not in ("Customer Delivery", "Warehouse Transfer")
		):
			frappe.throw(_("Allocation routes require submitted customer/depot loads in the same company."))
		for row in load.delivery_orders:
			if row.get("transport_demand"):
				demand = frappe.get_doc("Transport Demand", row.transport_demand)
				require_document_permission(demand, "read")
				if demand.company != allocation.company or demand.source_document_type == "Return Request":
					frappe.throw(
						_("Return pickups and cross-company demands require dependency-aware route planning.")
					)
			warehouse = (
				row.get("destination_warehouse") if load.movement_type == "Warehouse Transfer" else None
			)
			address = (
				get_warehouse_planning_data(warehouse).get("address")
				if warehouse
				else row.get("delivery_address")
			)
			if not address:
				frappe.throw(_("Every route destination must have a linked Address."))
			if warehouse:
				warehouse_doc = frappe.get_doc("Warehouse", warehouse)
				require_document_permission(warehouse_doc, "read")
				if (
					warehouse_doc.company != allocation.company
					or warehouse_doc.disabled
					or warehouse_doc.is_group
				):
					frappe.throw(
						_(
							"Receiving warehouses must be active, non-group warehouses in the allocation company."
						)
					)
			if address not in locations:
				require_document_permission(frappe.get_doc("Address", address), "read")
				locations[address] = get_address_planning_data(address)
			customer = row.get("customer") if not warehouse else None
			key = fingerprint([customer, warehouse, address])
			group = groups.setdefault(
				key,
				{
					"stop_key": key,
					"address": address,
					"customer": customer,
					"warehouse": warehouse,
					"source_rows": [],
				},
			)
			group["source_rows"].append(row.name)
			source_rows.append(
				[
					load.name,
					row.name,
					row.item_code,
					row.allocated_quantity,
					row.get("uom"),
					row.get("conversion_factor"),
					row.get("transport_demand"),
					key,
				]
			)
	vehicle = frappe.get_doc("Vehicle", allocation.vehicle) if allocation.vehicle else None
	if vehicle:
		require_document_permission(vehicle, "read")
	origin = next(iter(origins))
	origin_doc = frappe.get_doc("Warehouse", origin)
	require_document_permission(origin_doc, "read")
	if origin_doc.company != allocation.company or origin_doc.disabled or origin_doc.is_group:
		frappe.throw(_("The loading warehouse must be active and belong to the allocation company."))
	if not groups:
		frappe.throw(_("The selected loads have no delivery items to route."))
	if vehicle and vehicle.get("company") and vehicle.company != allocation.company:
		frappe.throw(_("The vehicle belongs to a different company."))
	root = {
		"company": allocation.company,
		"vehicle": allocation.vehicle,
		"driver": allocation.driver,
		"cost_structure": allocation.cost_structure,
		"departure": str(get_datetime(allocation.planned_reporting_time))
		if allocation.planned_reporting_time
		else None,
		"return": cint(allocation.get("route_return_to_start"))
		if return_to_start is None
		else int(return_to_start),
		"origin": origin,
		"origin_location": get_warehouse_planning_data(origin),
		"loads": [load.name for load in loads],
		"rows": source_rows,
		"locations": locations,
		"vehicle_costing": [
			vehicle.get(field)
			for field in (
				"vuna_transport_cost_structure",
				"vuna_vehicle_category",
				"vuna_expected_fuel_efficiency",
				"uom",
			)
		]
		if vehicle
		else [],
	}
	return {
		"fingerprint": fingerprint(root),
		"source_warehouse": origin,
		"stops": list(groups.values()),
		"loads": loads,
	}


def ordered_stops(doc, manifest):
	by_key = {row["stop_key"]: row for row in manifest["stops"]}
	keys = [row.stop_key for row in doc.get("route_stops") or []]
	if len(keys) != len(set(keys)) or set(keys) != set(by_key):
		frappe.throw(_("Route stops no longer match the selected loads. Get Route Stops again."))
	return [by_key[key] for key in keys]


def route_rows(stops):
	return [{**row, "source_rows": json.dumps(row["source_rows"])} for row in stops]


def validate_route(allocation):
	rows = allocation.get("route_stops") or []
	if not rows:
		allocation.route_status = "Unplanned"
		allocation.route_source_warehouse = None
		allocation.route_approved_by = allocation.route_approved_on = None
		allocation.route_manifest_fingerprint = None
		clear_road_distance(allocation)
		previous = allocation.get_doc_before_save()
		if previous and previous.get("route_stops"):
			clear_route_costs(allocation)
		return
	manifest = build_manifest(allocation)
	previous = allocation.get_doc_before_save()
	current_keys = [row.stop_key for row in rows]
	manifest_keys = {row["stop_key"] for row in manifest["stops"]}
	if (
		previous
		and allocation.docstatus == 0
		and (len(current_keys) != len(set(current_keys)) or set(current_keys) != manifest_keys)
	):
		# Let a planner save changed loads before Get Route Stops. Retain the
		# last saved sequence as outdated evidence, not client-supplied links.
		fields = ("stop_key", "address", "customer", "warehouse", "source_rows")
		allocation.set(
			"route_stops",
			[{field: row.get(field) for field in fields} for row in previous.get("route_stops") or []],
		)
		allocation.route_status = "Outdated"
		allocation.route_approved_by = allocation.route_approved_on = None
		allocation.route_manifest_fingerprint = previous.get("route_manifest_fingerprint")
		allocation.route_source_warehouse = manifest["source_warehouse"]
		clear_route_costs(allocation)
		clear_road_distance(allocation)
		return
	stops = ordered_stops(allocation, manifest)
	# Rebuild every reference from the authoritative loads, not client child values.
	for row, values in zip(rows, route_rows(stops), strict=True):
		row.update(values)
	unchanged = bool(
		previous
		and not allocation.get("route_preview_token")
		and previous.get("route_status") == "Approved"
		and previous.get("route_manifest_fingerprint") == manifest["fingerprint"]
		and [row.stop_key for row in previous.get("route_stops") or []] == [row.stop_key for row in rows]
	)
	if allocation.get("route_manifest_fingerprint") != manifest["fingerprint"]:
		allocation.route_status = "Outdated"
		allocation.route_approved_by = allocation.route_approved_on = None
	elif allocation.flags.approve_route:
		require_routing_role(frappe.get_cached_doc("Transport Settings"))
		allocation.route_status = "Approved"
		allocation.route_approved_by = frappe.session.user
		allocation.route_approved_on = now_datetime()
	elif unchanged:
		allocation.route_status = "Approved"
		allocation.route_approved_by = previous.route_approved_by
		allocation.route_approved_on = previous.route_approved_on
	else:
		allocation.route_status = "Proposed"
		allocation.route_approved_by = allocation.route_approved_on = None
	allocation.route_source_warehouse = manifest["source_warehouse"]
	if allocation.route_status == "Outdated":
		clear_road_distance(allocation)
	else:
		validate_road_distance(allocation, previous, manifest)
	# Draft estimates must never survive an edited or unapproved journey.
	if allocation.docstatus == 0:
		clear_route_costs(allocation)
	if allocation.docstatus == 1 and allocation.route_status != "Approved":
		frappe.throw(_("Approve the current allocation route before submitting."))


def clear_road_distance(allocation):
	allocation.route_distance_km = 0
	for field in (
		"route_distance_provider",
		"route_calculated_on",
		"route_distance_fingerprint",
		"route_preview_token",
	):
		allocation.set(field, None)


def validate_road_distance(allocation, previous, manifest):
	from vuna_transport.services.routing.route_preview_receipt import verify_receipt

	keys = [row.stop_key for row in allocation.route_stops]
	binding = fingerprint([manifest["fingerprint"], keys])
	if allocation.get("route_preview_token"):
		receipt = verify_receipt(
			allocation.route_preview_token, allocation, previous, manifest["fingerprint"], keys
		)
		allocation.route_distance_km = receipt["distance_km"]
		allocation.route_distance_provider = "Google"
		allocation.route_calculated_on = receipt["calculated_on"]
		allocation.route_distance_fingerprint = binding
		allocation.route_preview_token = None
	elif previous and previous.get("route_distance_fingerprint") == binding:
		for field in (
			"route_distance_km",
			"route_distance_provider",
			"route_calculated_on",
			"route_distance_fingerprint",
		):
			allocation.set(field, previous.get(field))
	else:
		clear_road_distance(allocation)


def clear_route_costs(allocation):
	for field in (
		"estimated_distance",
		"estimated_marginal_cost",
		"estimated_fixed_cost",
		"estimated_full_cost",
	):
		allocation.set(field, 0)
	for field in (
		"estimated_distance_uom",
		"distance_source",
		"estimated_cost_currency",
		"estimated_on",
		"cost_structure_assignment",
		"cost_warnings",
	):
		allocation.set(field, None)
	allocation.set("cost_breakdown", [])


def require_approved_route(allocation, loads=None):
	if not allocation.get("route_stops"):
		return None
	manifest = build_manifest(allocation, loads)
	if (
		allocation.get("route_status") != "Approved"
		or allocation.route_manifest_fingerprint != manifest["fingerprint"]
	):
		frappe.throw(
			_(
				"The allocation route is not approved or its inputs changed. Review the allocation before creating a trip."
			)
		)
	return ordered_stops(allocation, manifest)


@frappe.whitelist(methods=["POST"])
def prepare_route(allocation_name: str):
	allocation = frappe.get_doc("Vehicle Allocation", allocation_name)
	require_document_permission(allocation, "write")
	require_routing_role(frappe.get_cached_doc("Transport Settings"))
	if allocation.docstatus != 0:
		frappe.throw(_("Only draft allocations can prepare a new route plan."))
	manifest = build_manifest(allocation)
	by_key = {row["stop_key"]: row for row in manifest["stops"]}
	keys = [row.stop_key for row in allocation.get("route_stops") or []]
	stops = (
		[by_key[key] for key in keys]
		if len(keys) == len(set(keys)) and set(keys) == set(by_key)
		else manifest["stops"]
	)
	return {
		"allocation_route_rows": route_rows(stops),
		"return_to_start": cint(allocation.get("route_return_to_start")),
		"route_fingerprint": manifest["fingerprint"],
		"source_modified": str(allocation.modified),
		"source_warehouse": manifest["source_warehouse"],
	}


@frappe.whitelist(methods=["POST"])
def approve_route(allocation_name: str, expected_fingerprint: str, expected_modified: str):
	frappe.db.get_value("Vehicle Allocation", allocation_name, "name", for_update=True)
	allocation = frappe.get_doc("Vehicle Allocation", allocation_name)
	require_document_permission(allocation, "write")
	require_routing_role(frappe.get_cached_doc("Transport Settings"))
	if allocation.docstatus != 0:
		frappe.throw(_("Only a draft allocation route can be approved."))
	if str(allocation.modified) != expected_modified:
		frappe.throw(
			_("The allocation changed after your review. Reload and review its route before approval.")
		)
	manifest = build_manifest(allocation)
	if (
		expected_fingerprint != manifest["fingerprint"]
		or allocation.route_manifest_fingerprint != expected_fingerprint
	):
		frappe.throw(_("The route inputs changed. Get Route Stops again, save and review before approval."))
	ordered_stops(allocation, manifest)
	allocation.flags.approve_route = True
	allocation.save()
	return allocation.route_status


def order_trip_stops(trip, allocation, loads, specs=None):
	approved = require_approved_route(allocation, loads) if allocation else None
	if approved:
		ranks = {name: index for index, stop in enumerate(approved) for name in stop["source_rows"]}
	else:
		# Even without allocation-level approval, retain load row order rather
		# than creation timestamps of the stock documents.
		ranks = {
			row.name: index for index, row in enumerate(row for load in loads for row in load.delivery_orders)
		}
	by_task = {spec["task_key"]: spec for spec in specs or []}

	def rank(stop):
		sources = []
		if stop.get("delivery_note"):
			note = frappe.get_doc("Delivery Note", stop.delivery_note)
			require_document_permission(note, "read")
			sources.extend(
				row.vuna_delivery_load_order
				for row in note.get("items")
				if row.get("vuna_delivery_load_order")
			)
		for key in frappe.parse_json(stop.get("vuna_task_keys") or "[]"):
			if key in by_task:
				sources.extend(item["load_item"] for item in by_task[key]["items"])
		known = [ranks[name] for name in sources if name in ranks]
		if approved and (not known or any(name not in ranks for name in sources)):
			frappe.throw(_("Trip stops do not match the approved allocation route."))
		return min(known) if known else len(ranks)

	# Do not reorder pickup-dependent journeys when there is no approved plan.
	if not approved and any(
		spec["task_type"] not in ("Customer Delivery", "Transfer Receipt") for spec in specs or []
	):
		return
	trip.delivery_stops.sort(key=rank)
	for index, stop in enumerate(trip.delivery_stops, 1):
		stop.idx = index
	if approved:
		trip.vuna_route_return_to_start = cint(allocation.route_return_to_start)
