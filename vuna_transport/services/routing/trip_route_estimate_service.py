"""Trip-specific road estimates; never overwrite the allocation planning baseline."""

import frappe
from frappe import _
from frappe.utils import cint, get_datetime

from vuna_transport.services.costing.transport_cost_estimation_service import estimate_transport_journey
from vuna_transport.services.execution.lineage_service import get_delivery_trip_loads
from vuna_transport.services.routing.allocation_route_service import build_manifest, fingerprint
from vuna_transport.services.routing.location_service import get_address_planning_data
from vuna_transport.services.routing.route_preview_receipt import verify_receipt
from vuna_transport.services.shared.security_service import require_document_permission

DISTANCE_FIELDS = (
	"vuna_route_distance_km",
	"vuna_route_provider",
	"vuna_route_calculated_on",
	"vuna_route_estimate_fingerprint",
)
COST_FIELDS = (
	"vuna_route_cost_currency",
	"vuna_route_cost_structure",
	"vuna_route_cost_assignment",
	"vuna_route_marginal_cost",
	"vuna_route_fixed_cost",
	"vuna_route_full_cost",
	"vuna_route_cost_breakdown",
	"vuna_route_cost_warnings",
)


def build_trip_manifest(doc, *, return_to_start=None, loads=None):
	loads = get_delivery_trip_loads(doc) if loads is None else loads
	vehicle = frappe.get_doc("Vehicle", doc.vehicle)
	require_document_permission(vehicle, "read")
	context = frappe._dict(
		company=doc.company,
		vehicle=doc.vehicle,
		driver=doc.driver,
		cost_structure=vehicle.get("vuna_transport_cost_structure"),
		planned_reporting_time=doc.departure_time,
		route_return_to_start=cint(doc.get("vuna_route_return_to_start"))
		if return_to_start is None
		else int(return_to_start),
	)
	manifest = build_manifest(context, loads)
	stops = []
	destinations = {}
	for stop in doc.delivery_stops:
		require_document_permission(frappe.get_doc("Address", stop.address), "read")
		geography = get_address_planning_data(stop.address)
		destinations[stop.address] = frappe._dict(geography, cost_key=stop.address)
		stops.append(
			[
				stop.name,
				stop.address,
				stop.get("customer"),
				stop.get("delivery_note"),
				stop.get("vuna_task_keys"),
				stop.get("vuna_delivery_load"),
				geography,
			]
		)
	return {
		"loads": loads,
		"destinations": list(destinations.values()),
		"fingerprint": fingerprint(
			[
				manifest["fingerprint"],
				doc.get("vuna_vehicle_allocation"),
				sorted(stops, key=lambda row: row[0]),
			]
		),
	}


def _clear_estimate(doc, status):
	for field in (*DISTANCE_FIELDS, *COST_FIELDS):
		doc.set(field, None)
	doc.vuna_route_preview_token = None
	doc.vuna_route_estimate_status = status
	if status == "Outdated":
		doc.vuna_route_cost_warnings = _(
			"Trip routing inputs changed. Preview and apply the road route again to update distance and costs."
		)


def validate_trip_estimate(doc, method=None):
	previous = doc.get_doc_before_save()
	token = doc.get("vuna_route_preview_token")
	had_estimate = bool(previous and previous.get("vuna_route_estimate_fingerprint"))
	if not token and not had_estimate:
		_clear_estimate(
			doc,
			"Outdated"
			if previous and previous.get("vuna_route_estimate_status") == "Outdated"
			else "Unplanned",
		)
		return
	if doc.get("vuna_actual_start") or doc.docstatus not in (0, 1):
		if token:
			frappe.throw(_("Apply road estimates only to a draft trip before execution."))
		for field in (*DISTANCE_FIELDS, *COST_FIELDS, "vuna_route_estimate_status"):
			doc.set(field, previous.get(field))
		return
	if token and (doc.docstatus != 0 or any(stop.get("visited") for stop in doc.delivery_stops)):
		frappe.throw(_("Apply road estimates only to a draft trip before execution."))
	if not doc.vehicle or not doc.delivery_stops:
		_clear_estimate(doc, "Outdated")
		return
	manifest = build_trip_manifest(doc)
	keys = [stop.name for stop in doc.delivery_stops]
	binding = fingerprint([manifest["fingerprint"], keys])
	if token:
		# Repeat all routing guards on application, not only when Google was called.
		from vuna_transport.services.routing.road_routing_service import _validate_optimization
		from vuna_transport.services.routing.routing_policy_service import require_routing_role

		require_routing_role(frappe.get_cached_doc("Transport Settings"))
		_validate_optimization(doc, "Delivery Trip")
		receipt = verify_receipt(token, doc, previous, manifest["fingerprint"], keys)
		doc.vuna_route_distance_km = receipt["distance_km"]
		doc.vuna_route_provider = "Google"
		doc.vuna_route_calculated_on = receipt["calculated_on"]
		doc.vuna_route_estimate_fingerprint = binding
		doc.vuna_route_preview_token = None
	elif previous.get("vuna_route_estimate_fingerprint") == binding:
		for field in DISTANCE_FIELDS:
			doc.set(field, previous.get(field))
		# Unchanged saves keep the previously reviewed estimate, without repricing
		# unexpectedly after a tariff change or an execution-status update.
		for field in COST_FIELDS:
			doc.set(field, previous.get(field))
		doc.vuna_route_estimate_status = previous.vuna_route_estimate_status
		return
	else:
		_clear_estimate(doc, "Outdated")
		return
	settings = frappe.get_cached_doc("Transport Settings")
	for field in COST_FIELDS:
		doc.set(field, None)
	if not settings.get("enable_transport_costing"):
		doc.vuna_route_estimate_status = "Distance Only"
		doc.vuna_route_cost_warnings = _(
			"Transport costing is disabled. The road distance is saved without a cost estimate."
		)
		return
	result = estimate_transport_journey(
		manifest["loads"],
		doc.vehicle,
		distance=doc.vuna_route_distance_km,
		distance_uom="Kilometer",
		distance_source="Routing Provider",
		as_of_date=get_datetime(doc.departure_time),
		journey_destinations=manifest.get("destinations"),
	)
	doc.vuna_route_cost_currency = result.get("currency")
	doc.vuna_route_cost_structure = result.get("cost_structure")
	doc.vuna_route_cost_assignment = result.get("cost_structure_assignment")
	doc.vuna_route_marginal_cost = result.get("marginal_cost")
	doc.vuna_route_fixed_cost = result.get("fixed_cost")
	doc.vuna_route_full_cost = result.get("estimated_full_cost")
	doc.vuna_route_cost_breakdown = result.get("components") or []
	doc.vuna_route_cost_warnings = "\n".join(result.get("warnings") or [])
	doc.vuna_route_estimate_status = "Current" if result.get("cost_structure_assignment") else "Needs Review"
