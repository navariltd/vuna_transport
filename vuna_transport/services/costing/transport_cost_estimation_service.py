"""Read-only transport cost estimates based on configured profiles and rates."""

import math
from dataclasses import dataclass, replace
from datetime import date

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, nowdate

from vuna_transport.services.routing.distance_estimation_service import estimate_delivery_load_distance
from vuna_transport.services.routing.load_location_service import get_load_planning_locations
from vuna_transport.services.shared.measurement_service import convert_measurement, get_transport_uoms
from vuna_transport.services.shared.security_service import (
	require_company_access,
	require_document_permission,
)


@dataclass
class CostContext:
	company: str
	load: object
	vehicle: object
	date: object
	distance: float
	distance_uom: str
	distance_source: str
	destination: str | None
	destinations: list[str]
	destination_geographies: list[object] | None = None
	source_warehouse: str | None = None
	planning_warnings: list[str] | None = None


@frappe.whitelist()
def estimate_transport_cost(
	delivery_load: str | Document,
	vehicle: str | Document,
	*,
	as_of_date: str | date | None = None,
	distance: float | str | None = None,
	distance_source: str | None = None,
):
	"""Estimate a load's cost for a vehicle without changing any documents."""
	load = _resolve_document("Transport Load", delivery_load)
	vehicle_doc = _resolve_document("Vehicle", vehicle)
	require_document_permission(load, "read")
	require_document_permission(vehicle_doc, "read")

	if load.company and getattr(vehicle_doc, "company", None) and load.company != vehicle_doc.company:
		frappe.throw(_("Transport Load and Vehicle must belong to the same Company."))
	require_company_access(load.company)

	uoms = get_transport_uoms()
	context = _build_context(
		load,
		vehicle_doc,
		uoms,
		as_of_date=as_of_date,
		distance=distance,
		distance_source=distance_source,
	)
	profile = get_applicable_cost_structure_assignment(vehicle_doc, load.company, context.date)
	warnings = list(context_warnings(context))
	if not profile:
		if not getattr(vehicle_doc, "vuna_transport_cost_structure", None):
			warnings.append(
				_("Vehicle {0} has no Transport Cost Structure selected.").format(vehicle_doc.name)
			)
		else:
			warnings.append(
				_(
					"No active Cost Structure Assignment applies to Vehicle {0} for Cost Structure {1}."
				).format(vehicle_doc.name, vehicle_doc.vuna_transport_cost_structure)
			)
		return _empty_estimate(vehicle_doc, context, warnings)

	components = []
	for component in profile.components:
		result = estimate_cost_component(component, context, profile)
		components.append(result)
		warnings.extend(result.pop("warnings", []))

	marginal_cost = sum(flt(row["amount"]) for row in components if row["basis"] != "Monthly Fixed")
	fixed_cost = sum(flt(row["amount"]) for row in components if row["basis"] == "Monthly Fixed")
	return {
		"vehicle": vehicle_doc.name,
		"vehicle_category": getattr(vehicle_doc, "vuna_vehicle_category", None),
		"cost_structure": profile.cost_structure,
		"cost_structure_assignment": profile.name,
		"currency": profile.currency,
		"distance": context.distance,
		"distance_uom": context.distance_uom,
		"distance_source": context.distance_source,
		"components": components,
		"marginal_cost": flt(marginal_cost),
		"fixed_cost": flt(fixed_cost),
		"allocated_fixed_cost": 0,
		"unallocated_fixed_cost": flt(fixed_cost),
		"estimated_full_cost": flt(marginal_cost),
		"warnings": _unique(warnings),
	}


def get_applicable_cost_structure_assignment(vehicle, company, date=None):
	"""Resolve a single profile using explicit vehicle and scope precedence."""
	date = getdate(date or nowdate())
	cost_structure = getattr(vehicle, "vuna_transport_cost_structure", None)
	if not cost_structure:
		return None

	profiles = frappe.get_all(
		"Transport Cost Structure Assignment",
		filters={
			"company": company,
			"cost_structure": cost_structure,
			"disabled": 0,
			"docstatus": 1,
		},
		fields=["name", "vehicle", "supplier", "vehicle_category", "valid_from", "valid_to", "docstatus"],
	)
	vehicle_category = getattr(vehicle, "vuna_vehicle_category", None)
	supplier = getattr(vehicle, "vuna_supplier", None)
	candidates = []
	for profile in profiles:
		if not _profile_is_active(profile, company, date):
			continue
		# Scope restrictions remain restrictions even when another field matches.
		# A different vehicle/supplier must not fall back to a category match.
		if profile.vehicle and profile.vehicle != vehicle.name:
			continue
		if profile.supplier and profile.supplier != supplier:
			continue
		if profile.vehicle_category and profile.vehicle_category != vehicle_category:
			continue
		if profile.vehicle and profile.vehicle == vehicle.name:
			precedence = 4
		elif (
			profile.supplier and profile.supplier == supplier and profile.vehicle_category == vehicle_category
		):
			precedence = 3
		elif profile.supplier and profile.supplier == supplier and not profile.vehicle_category:
			precedence = 2
		elif profile.vehicle_category and profile.vehicle_category == vehicle_category:
			precedence = 1
		elif not profile.vehicle and not profile.supplier and not profile.vehicle_category:
			precedence = 0
		else:
			continue
		candidates.append((precedence, getdate(profile.valid_from), profile.name))
	if not candidates:
		return None
	best = max(candidates)
	return frappe.get_doc("Transport Cost Structure Assignment", best[2])


def get_applicable_cost_structure_assignment_for_load(load, cost_structure, date=None, vehicle_category=None):
	"""Resolve a load-level assignment without requiring a physical Vehicle."""
	date = getdate(date or getattr(load, "dispatch_date", None) or nowdate())
	profiles = frappe.get_all(
		"Transport Cost Structure Assignment",
		filters={
			"company": load.company,
			"cost_structure": cost_structure,
			"disabled": 0,
			"docstatus": 1,
		},
		fields=["name", "vehicle", "supplier", "vehicle_category", "valid_from", "valid_to"],
	)
	candidates = []
	for profile in profiles:
		if not _profile_is_active(profile, load.company, date):
			continue
		# A vehicle- or supplier-specific assignment cannot be selected before
		# the actual transport resource is known.
		if profile.vehicle or profile.supplier:
			continue
		if profile.vehicle_category and profile.vehicle_category != vehicle_category:
			continue
		precedence = 1 if profile.vehicle_category else 0
		candidates.append((precedence, getdate(profile.valid_from), profile.name))
	if not candidates:
		return None
	best = max(candidates)
	return frappe.get_doc("Transport Cost Structure Assignment", best[2])


def estimate_transport_cost_for_cost_structure(
	delivery_load, cost_structure, *, vehicle_category=None, as_of_date=None
):
	"""Estimate a load against a Cost Structure before a physical Vehicle is selected."""
	load = _resolve_document("Transport Load", delivery_load)
	require_document_permission(load, "read")
	require_company_access(load.company)
	uoms = get_transport_uoms()
	vehicle = frappe._dict(
		name=None,
		vuna_vehicle_category=vehicle_category,
		vuna_expected_fuel_efficiency=None,
	)
	context = _build_context(
		load,
		vehicle,
		uoms,
		as_of_date=as_of_date,
		distance=None,
		distance_source=None,
	)
	profile = get_applicable_cost_structure_assignment_for_load(
		load, cost_structure, context.date, vehicle_category
	)
	warnings = list(context_warnings(context))
	if not profile:
		warnings.append(
			_("No active Cost Structure Assignment applies to {0} for this load.").format(cost_structure)
		)
		return _empty_structure_estimate(cost_structure, vehicle_category, context, warnings)

	components = []
	for component in profile.components:
		result = estimate_cost_component(component, context, profile)
		components.append(result)
		warnings.extend(result.pop("warnings", []))
	marginal_cost = sum(flt(row["amount"]) for row in components if row["basis"] != "Monthly Fixed")
	fixed_cost = sum(flt(row["amount"]) for row in components if row["basis"] == "Monthly Fixed")
	return {
		"vehicle": None,
		"vehicle_category": vehicle_category,
		"cost_structure": cost_structure,
		"cost_structure_assignment": profile.name,
		"currency": profile.currency,
		"distance": context.distance,
		"distance_uom": context.distance_uom,
		"distance_source": context.distance_source,
		"components": components,
		"marginal_cost": flt(marginal_cost),
		"fixed_cost": flt(fixed_cost),
		"allocated_fixed_cost": 0,
		"unallocated_fixed_cost": flt(fixed_cost),
		"estimated_full_cost": flt(marginal_cost),
		"warnings": _unique(warnings),
	}


def estimate_transport_cost_for_loads(delivery_loads, vehicle):
	"""Estimate one vehicle allocation containing multiple Transport Loads.

	Each load keeps its own destination-rate context. The allocation snapshot
	combines the results and explicitly warns that the distance is not a
	route-optimized total.
	"""
	load_documents = [_resolve_document("Transport Load", load) for load in (delivery_loads or [])]
	if not load_documents:
		frappe.throw(_("At least one Transport Load is required for a combined estimate."))
	vehicle_doc = _resolve_document("Vehicle", vehicle)
	results = [estimate_transport_cost(load, vehicle_doc) for load in load_documents]
	assignments = {
		row.get("cost_structure_assignment") for row in results if row.get("cost_structure_assignment")
	}
	structures = {row.get("cost_structure") for row in results if row.get("cost_structure")}
	currencies = {row.get("currency") for row in results if row.get("currency")}
	warnings = _unique(warning for result in results for warning in result.get("warnings", []))
	if len(load_documents) > 1:
		warnings.append(
			_("Combined distance sums the individual load estimates; it is not a route-optimized distance.")
		)
	if len(assignments) > 1:
		warnings.append(_("Attached loads resolved to different Cost Structure Assignments."))
	if len(currencies) > 1:
		frappe.throw(
			_(
				"Attached loads use different cost currencies. Configure one currency before combining estimates."
			)
		)
	if len(load_documents) > 1 and any(
		component.get("basis") == "Flat Per Trip"
		for result in results
		for component in result.get("components", [])
	):
		warnings.append(
			_(
				"Flat Per Trip charges are estimated per load. Review shared-trip charges before using the combined total."
			)
		)

	components = []
	for load, result in zip(load_documents, results, strict=False):
		for component in result.get("components", []):
			components.append({"delivery_load": load.name, **component})
	# Monthly fixed costs belong to the assignment, not to every attached load.
	fixed_cost = results[0].get("fixed_cost", 0)
	marginal_cost = sum(flt(result.get("marginal_cost")) for result in results)
	return {
		"vehicle": vehicle_doc.name,
		"vehicle_category": getattr(vehicle_doc, "vuna_vehicle_category", None),
		"cost_structure": next(iter(structures), None) if len(structures) == 1 else None,
		"cost_structure_assignment": next(iter(assignments), None) if len(assignments) == 1 else None,
		"currency": next(iter(currencies), None) if len(currencies) == 1 else None,
		"distance": sum(
			_distance_in_rate_uom(
				result.get("distance"), result.get("distance_uom"), results[0].get("distance_uom")
			)
			for result in results
		),
		"distance_uom": results[0].get("distance_uom"),
		"distance_source": "Estimated" if len(load_documents) > 1 else results[0].get("distance_source"),
		"components": components,
		"marginal_cost": flt(marginal_cost),
		"fixed_cost": flt(fixed_cost),
		"allocated_fixed_cost": 0,
		"unallocated_fixed_cost": flt(fixed_cost),
		"estimated_full_cost": flt(marginal_cost),
		"warnings": _unique(warnings),
	}


def estimate_transport_journey(
	loads, vehicle, *, distance, distance_uom="Kilometer", distance_source="Planned Distance", as_of_date=None
):
	"""Cost one journey, charging shared components once and retaining destination rates."""
	loads = [_resolve_document("Transport Load", load) for load in loads]
	if not loads:
		frappe.throw(_("Select at least one Transport Load for journey costing."))
	try:
		distance = float(distance)
	except (ValueError, TypeError, OverflowError):
		distance = -1
	if not math.isfinite(distance) or distance < 0:
		frappe.throw(_("Journey distance must be a finite, non-negative number."))
	if not distance_uom:
		frappe.throw(_("Select a Distance UOM for journey costing."))
	convert_measurement(1, distance_uom, "Kilometer", _("journey distance"))
	vehicle = _resolve_document("Vehicle", vehicle)
	require_document_permission(vehicle, "read")
	contexts = []
	for load in loads:
		require_document_permission(load, "read")
		require_company_access(load.company)
		if load.company != loads[0].company or (vehicle.get("company") and vehicle.company != load.company):
			frappe.throw(_("Journey loads and vehicle must belong to the same company."))
		context = _build_context(
			load,
			vehicle,
			get_transport_uoms(),
			as_of_date=as_of_date,
			distance=0,
			distance_source=distance_source,
		)
		contexts.append(
			replace(context, distance=distance, distance_uom=distance_uom, distance_source=distance_source)
		)
	context = contexts[0]
	profile = get_applicable_cost_structure_assignment(vehicle, context.company, context.date)
	warnings = []
	if not profile:
		return _empty_estimate(
			vehicle, context, [_("No active Cost Structure Assignment applies to this journey.")]
		)
	components = []
	for component in profile.components:
		applicable = [context]
		if component.calculation_basis == "Destination Rate":
			applicable = []
			seen = set()
			for load_context in contexts:
				for geography in load_context.destination_geographies or []:
					key = (load_context.source_warehouse, geography.cost_key)
					if key in seen:
						continue
					seen.add(key)
					applicable.append(
						replace(
							load_context,
							destination=geography.cost_key,
							destinations=[geography.cost_key],
							destination_geographies=[geography],
						)
					)
			if not applicable:
				warnings.append(_("No destination is available for destination-based journey costing."))
		for item_context in applicable:
			result = estimate_cost_component(component, item_context, profile)
			warnings.extend(result.pop("warnings", []))
			components.append(result)
	marginal = sum(flt(row["amount"]) for row in components if row["basis"] != "Monthly Fixed")
	fixed = sum(flt(row["amount"]) for row in components if row["basis"] == "Monthly Fixed")
	return {
		"vehicle": vehicle.name,
		"vehicle_category": vehicle.get("vuna_vehicle_category"),
		"cost_structure": profile.cost_structure,
		"cost_structure_assignment": profile.name,
		"currency": profile.currency,
		"distance": distance,
		"distance_uom": distance_uom,
		"distance_source": distance_source,
		"components": components,
		"marginal_cost": marginal,
		"fixed_cost": fixed,
		"allocated_fixed_cost": 0,
		"unallocated_fixed_cost": fixed,
		"estimated_full_cost": marginal,
		"warnings": _unique(warnings),
	}


def estimate_cost_component(component, context, profile=None):
	"""Calculate one configured component and return an explainable result."""
	basis = component.calculation_basis
	rate = flt(getattr(component, "rate", None))
	warnings = []
	quantity = None
	uom = None
	if basis == "Flat Per Trip":
		amount = _flat_amount(component)
		explanation = _explanation(component, amount)
	elif basis == "Per Distance":
		quantity = _distance_in_rate_uom(context.distance, context.distance_uom, component.rate_uom)
		uom = component.rate_uom or context.distance_uom
		amount = quantity * rate
		if context.distance_source in {"Estimated", "Haversine", "Geographic Estimate", ""}:
			warnings.append(_("Distance Charge uses an estimated distance."))
		explanation = _multiplication_explanation(quantity, uom, rate, amount)
	elif basis == "Per Fuel Quantity":
		quantity, uom = _fuel_quantity(context, component)
		amount = quantity * rate
		if not quantity:
			warnings.append(
				_("Fuel cost could not be estimated because distance or fuel efficiency is missing.")
			)
		explanation = _multiplication_explanation(quantity, uom, rate, amount)
	elif basis == "Monthly Fixed":
		amount = _flat_amount(component)
		warnings.append(_("Monthly Fixed cost is reported separately and is not included in marginal cost."))
		explanation = _explanation(component, amount)
	elif basis == "Destination Rate":
		rate_amount = _resolve_destination_rate(context, profile)
		amount = flt(rate_amount)
		if len(context.destinations) != 1:
			warnings.append(
				_("Destination Rate requires exactly one movement destination; no rate was applied.")
			)
		elif rate_amount is None:
			warnings.append(
				_(
					"No matching Destination Rate is configured for this movement's destination and source warehouse."
				)
			)
		explanation = _explanation(component, amount)
	elif basis == "Manual / Actual":
		amount = _flat_amount(component)
		warnings.append(_("Manual / Actual uses the configured amount."))
		explanation = _explanation(component, amount)
	else:
		frappe.throw(_("Unsupported calculation basis: {0}").format(basis))

	return {
		"component": component.cost_component,
		"basis": basis,
		"quantity": quantity,
		"uom": uom,
		"rate": rate,
		"amount": flt(amount),
		"cost_nature": component.cost_nature,
		"estimated": basis in {"Per Distance", "Per Fuel Quantity", "Manual / Actual", "Destination Rate"},
		"explanation": explanation,
		"warnings": warnings,
	}


def _build_context(load, vehicle, uoms, *, as_of_date, distance, distance_source):
	load_distance = distance if distance is not None else getattr(load, "estimated_distance", None)
	source = distance_source or getattr(load, "distance_estimate_method", None)
	distance_uom = getattr(load, "distance_uom", None) or uoms.distance_uom or "Kilometer"
	planning_warnings = []
	if distance is None and source == "Geographic Estimate":
		planning_warnings.extend((getattr(load, "distance_estimate_warning", None) or "").splitlines())
	if distance is None and not load_distance:
		distance_estimate = estimate_delivery_load_distance(load)
		planning_warnings.extend(distance_estimate.get("warnings", []))
		if distance_estimate["available"]:
			load_distance = convert_measurement(
				distance_estimate["distance_km"], "Kilometer", distance_uom, _("transport distance")
			)
			source = distance_source or "Haversine"
	source = source or ("Planned Distance" if load_distance else "Estimated")
	if source in {"Haversine", "Geographic Estimate"}:
		planning_warnings.append(
			_(
				"The geographic estimate excludes empty repositioning and return journeys. Set a planned distance if these must be included."
			)
		)
	_origin, endpoints = get_load_planning_locations(load)
	destination_geographies = [row for row in endpoints if row.get("cost_key")]
	addresses = [row.cost_key for row in destination_geographies]
	return CostContext(
		company=load.company,
		load=load,
		vehicle=vehicle,
		date=getdate(as_of_date or getattr(load, "dispatch_date", None) or nowdate()),
		distance=flt(load_distance),
		distance_uom=distance_uom,
		distance_source=source,
		destination=addresses[0] if len(addresses) == 1 else None,
		destinations=addresses,
		destination_geographies=destination_geographies,
		source_warehouse=getattr(load, "source_warehouse", None),
		planning_warnings=planning_warnings,
	)


def context_warnings(context):
	warnings = list(context.planning_warnings or [])
	if not context.distance:
		warnings.append(_("No planned distance is available."))
	if not context.destinations:
		warnings.append(_("No movement destination is available for destination-based costing."))
	return warnings


def _profile_is_active(profile, company, date):
	return (
		profile.company == company
		and getattr(profile, "docstatus", 0) == 1
		and not profile.disabled
		and getdate(profile.valid_from) <= date
		and (not profile.valid_to or getdate(profile.valid_to) >= date)
	)


def _distance_in_rate_uom(distance, from_uom, to_uom):
	if not distance or not to_uom or from_uom == to_uom:
		return flt(distance)
	return flt(convert_measurement(distance, from_uom, to_uom, _("transport distance")))


def _fuel_quantity(context, component):
	efficiency = flt(getattr(context.vehicle, "vuna_expected_fuel_efficiency", None))
	if efficiency <= 0 or context.distance <= 0:
		return 0, component.rate_uom or "Litre"
	efficiency_uom = getattr(context.vehicle, "uom", None)
	distance = _distance_in_rate_uom(context.distance, context.distance_uom, efficiency_uom)
	uom = component.rate_uom or "Litre"
	return convert_measurement(distance / efficiency, "Litre", uom, _("fuel quantity")), uom


def _destination_rate(component, context, profile):
	return flt(_resolve_destination_rate(context, profile))


def _resolve_destination_rate(context, profile):
	if not context.destination or not profile:
		return None
	if len(context.destination_geographies or []) != 1:
		return _legacy_destination_rate(context, profile)
	geography = context.destination_geographies[0]
	candidates = []
	for row in getattr(profile, "destination_rates", []) or []:
		if getattr(row, "source_warehouse", None) and row.source_warehouse != context.source_warehouse:
			continue
		if not row.territory or row.territory != geography.territory:
			continue
		if row.county and row.county != geography.county:
			continue
		if row.town and row.town != geography.town:
			continue
		specificity = (
			int(bool(getattr(row, "source_warehouse", None))) * 10
			+ int(bool(row.county))
			+ int(bool(row.town))
		)
		candidates.append((specificity, flt(row.rate)))
	if candidates:
		return max(candidates)[1]
	return _legacy_destination_rate(context, profile)


def _legacy_destination_rate(context, profile):
	for row in getattr(profile, "destination_rates", []) or []:
		if getattr(row, "destination_key", None) == context.destination and (
			not getattr(row, "source_warehouse", None) or row.source_warehouse == context.source_warehouse
		):
			return flt(row.rate)
	return None


def _flat_amount(component):
	return flt(getattr(component, "flat_amount", None))


def _explanation(component, amount):
	return _("{0}: {1:.2f}").format(component.cost_component, amount)


def _multiplication_explanation(quantity, uom, rate, amount):
	return _("{0:.2f} {1} x {2:.2f} = {3:.2f}").format(quantity or 0, uom or "", rate, amount)


def _empty_estimate(vehicle, context, warnings):
	return {
		"vehicle": vehicle.name,
		"vehicle_category": getattr(vehicle, "vuna_vehicle_category", None),
		"cost_structure": None,
		"cost_structure_assignment": None,
		"currency": None,
		"distance": context.distance,
		"distance_uom": context.distance_uom,
		"distance_source": context.distance_source,
		"components": [],
		"marginal_cost": 0,
		"fixed_cost": 0,
		"allocated_fixed_cost": 0,
		"unallocated_fixed_cost": 0,
		"estimated_full_cost": 0,
		"warnings": _unique(warnings),
	}


def _empty_structure_estimate(cost_structure, vehicle_category, context, warnings):
	return {
		"vehicle": None,
		"vehicle_category": vehicle_category,
		"cost_structure": cost_structure,
		"cost_structure_assignment": None,
		"currency": None,
		"distance": context.distance,
		"distance_uom": context.distance_uom,
		"distance_source": context.distance_source,
		"components": [],
		"marginal_cost": 0,
		"fixed_cost": 0,
		"allocated_fixed_cost": 0,
		"unallocated_fixed_cost": 0,
		"estimated_full_cost": 0,
		"warnings": _unique(warnings),
	}


def _resolve_document(doctype, value):
	if hasattr(value, "doctype"):
		return value
	if not value:
		frappe.throw(_("{0} is required.").format(doctype))
	return frappe.get_doc(doctype, value)


def _unique(values):
	return list(dict.fromkeys(value for value in values if value))
