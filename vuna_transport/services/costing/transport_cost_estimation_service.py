"""Read-only transport cost estimates based on configured profiles and rates."""

from dataclasses import dataclass

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

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


@frappe.whitelist()
def estimate_transport_cost(
	delivery_load,
	vehicle,
	*,
	as_of_date=None,
	distance=None,
	distance_source=None,
):
	"""Estimate a load's cost for a vehicle without changing any documents."""
	load = _resolve_document("Delivery Load", delivery_load)
	vehicle_doc = _resolve_document("Vehicle", vehicle)
	require_document_permission(load, "read")
	require_document_permission(vehicle_doc, "read")

	if load.company and getattr(vehicle_doc, "company", None) and load.company != vehicle_doc.company:
		frappe.throw(_("Delivery Load and Vehicle must belong to the same Company."))
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
	profile = get_applicable_cost_profile(vehicle_doc, load.company, context.date)
	warnings = list(context_warnings(context))
	if not profile:
		if not getattr(vehicle_doc, "vuna_transport_costing_model", None):
			warnings.append(
				_("Vehicle {0} has no Transport Costing Model selected.").format(vehicle_doc.name)
			)
		else:
			warnings.append(
				_("No active Transport Cost Profile applies to Vehicle {0} for Costing Model {1}.").format(
					vehicle_doc.name, vehicle_doc.vuna_transport_costing_model
				)
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
		"costing_model": profile.costing_model,
		"cost_profile": profile.name,
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


def get_applicable_cost_profile(vehicle, company, date=None):
	"""Resolve a single profile using explicit vehicle and scope precedence."""
	date = getdate(date or nowdate())
	costing_model = getattr(vehicle, "vuna_transport_costing_model", None)
	if not costing_model:
		return None

	direct = getattr(vehicle, "vuna_transport_cost_profile", None)
	if direct:
		profile = frappe.get_doc("Transport Cost Profile", direct)
		if profile.costing_model != costing_model:
			frappe.throw(
				_("Transport Cost Profile {0} uses Costing Model {1}, but Vehicle {2} uses {3}.").format(
					profile.name, profile.costing_model, vehicle.name, costing_model
				)
			)
		if _profile_is_active(profile, company, date):
			return profile
		return None

	profiles = frappe.get_all(
		"Transport Cost Profile",
		filters={"company": company, "costing_model": costing_model, "disabled": 0},
		fields=["name", "vehicle", "supplier", "vehicle_category", "valid_from", "valid_to"],
	)
	vehicle_category = getattr(vehicle, "vuna_vehicle_category", None)
	supplier = getattr(vehicle, "vuna_supplier", None)
	candidates = []
	for profile in profiles:
		if not _profile_is_active(profile, company, date):
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
		candidates.append((precedence, profile.name))
	if not candidates:
		return None
	best_precedence = max(item[0] for item in candidates)
	best = [name for precedence, name in candidates if precedence == best_precedence]
	if len(best) > 1:
		frappe.throw(
			_("Multiple active Transport Cost Profiles match Vehicle {0}: {1}.").format(
				vehicle.name, ", ".join(best)
			)
		)
	return frappe.get_doc("Transport Cost Profile", best[0])


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
		if context.distance_source in {"Estimated", "Haversine", ""}:
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
		amount = _destination_rate(component, context, profile)
		if len(context.destinations) != 1:
			warnings.append(
				_("Destination Rate requires one destination; no rate was applied to this multi-stop load.")
			)
		explanation = _explanation(component, amount)
	elif basis == "Manual / Actual":
		amount = flt(getattr(component, "estimated_default_amount", None))
		warnings.append(_("Manual / Actual uses the configured estimated default amount."))
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
	source = distance_source or ("Planned Distance" if load_distance else "Estimated")
	addresses = sorted(
		{row.delivery_address for row in getattr(load, "delivery_orders", []) if row.delivery_address}
	)
	return CostContext(
		company=load.company,
		load=load,
		vehicle=vehicle,
		date=getdate(as_of_date or getattr(load, "dispatch_date", None) or nowdate()),
		distance=flt(load_distance),
		distance_uom=getattr(load, "distance_uom", None) or uoms.distance_uom,
		distance_source=source,
		destination=addresses[0] if len(addresses) == 1 else None,
		destinations=addresses,
	)


def context_warnings(context):
	warnings = []
	if not context.distance:
		warnings.append(_("No planned distance is available."))
	if not context.destinations:
		warnings.append(_("No delivery destination is available for destination-based costing."))
	return warnings


def _profile_is_active(profile, company, date):
	return (
		profile.company == company
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
	if not efficiency or not context.distance:
		return 0, component.rate_uom or "Litre"
	efficiency_uom = getattr(context.vehicle, "vuna_fuel_efficiency_uom", None)
	distance = _distance_in_rate_uom(context.distance, context.distance_uom, efficiency_uom)
	return distance / efficiency, component.rate_uom or "Litre"


def _destination_rate(component, context, profile):
	if not context.destination or not profile:
		return 0
	for row in getattr(profile, "destination_rates", []) or []:
		if row.destination_key == context.destination:
			return flt(row.rate)
	return 0


def _flat_amount(component):
	value = getattr(component, "flat_amount", None)
	return flt(value if value not in (None, "") else getattr(component, "estimated_default_amount", None))


def _explanation(component, amount):
	return _("{0}: {1:.2f}").format(component.cost_component, amount)


def _multiplication_explanation(quantity, uom, rate, amount):
	return _("{0:.2f} {1} x {2:.2f} = {3:.2f}").format(quantity or 0, uom or "", rate, amount)


def _empty_estimate(vehicle, context, warnings):
	return {
		"vehicle": vehicle.name,
		"vehicle_category": getattr(vehicle, "vuna_vehicle_category", None),
		"costing_model": None,
		"cost_profile": None,
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
