"""Vehicle-category capacity and fleet-availability rules shared by planning flows."""

from collections import Counter

import frappe
from frappe import _
from frappe.utils import flt, getdate

from vuna_transport.services.measurement_service import convert_measurement


CAPACITY_DIMENSIONS = ("weight", "volume", "pallets", "cases", "crates")


def get_vehicle_categories(load_uoms, capacity_buffer_percentage=0):
	rows = frappe.get_all(
		"Vehicle Category",
		filters={"disabled": 0},
		fields=[
			"name",
			"vehicle_category_name",
			"max_payload_weight",
			"max_payload_weight_uom",
			"max_volume",
			"max_volume_uom",
			"max_pallets",
			"max_cases",
			"max_crates",
			"refrigerated",
			"tail_lift",
			"body_type",
			"special_equipment",
		],
	)
	for category in rows:
		category.capacities = get_effective_capacities(
			category, load_uoms, capacity_buffer_percentage
		)
	return rows


def get_effective_capacities(category, load_uoms, buffer_percentage=0):
	weight_uom = load_uoms.get("weight_uom") or category.max_payload_weight_uom
	volume_uom = load_uoms.get("volume_uom") or category.max_volume_uom
	weight = convert_measurement(
		category.max_payload_weight,
		category.max_payload_weight_uom,
		weight_uom,
		_("vehicle payload capacity"),
	)
	volume = convert_measurement(
		category.max_volume,
		category.max_volume_uom,
		volume_uom,
		_("vehicle volume capacity"),
	)
	buffer_factor = 1 - flt(buffer_percentage) / 100
	return {
		"weight": flt(weight) * buffer_factor if weight else 0,
		"volume": flt(volume) * buffer_factor if volume else 0,
		"pallets": flt(category.max_pallets) * buffer_factor if category.max_pallets else 0,
		"cases": flt(category.max_cases) * buffer_factor if category.max_cases else 0,
		"crates": flt(category.max_crates) * buffer_factor if category.max_crates else 0,
	}


def evaluate_vehicle_category(totals, category, requirements=None):
	"""Return compatibility details without throwing, so suggestions remain explainable."""
	requirements = requirements or {}
	rejections = []
	if requirements.get("refrigeration_required") and not category.refrigerated:
		rejections.append(_("Refrigerated transport is required."))
	if requirements.get("tail_lift_required") and not category.tail_lift:
		rejections.append(_("A tail lift is required."))
	if requirements.get("required_body_type") and getattr(category, "body_type", None) != requirements.get(
		"required_body_type"
	):
		rejections.append(
			_("Vehicle body type {0} is required.").format(requirements["required_body_type"])
		)
	required_equipment = _normalise_equipment(requirements.get("special_equipment_required"))
	available_equipment = _normalise_equipment(getattr(category, "special_equipment", None))
	missing_equipment = sorted(required_equipment - available_equipment)
	if missing_equipment:
		rejections.append(
			_("Missing special equipment: {0}.").format(", ".join(missing_equipment))
		)

	utilization = {}
	for dimension in CAPACITY_DIMENSIONS:
		required = flt(totals.get(dimension))
		capacity = flt(category.capacities.get(dimension))
		utilization[dimension] = round(required / capacity * 100, 2) if capacity else None
		if capacity and required > capacity:
			rejections.append(
			_('{0} capacity exceeded: {1:.2f} required, {2:.2f} available.').format(
				dimension.title(), required, capacity
			)
		)
	return {
		"compatible": not rejections,
		"utilization": utilization,
		"rejections": rejections,
	}


def validate_vehicle_access_limit(category, maximum_vehicle_category):
	"""Return whether a candidate category is no larger than the address limit."""
	if not maximum_vehicle_category:
		return {"compatible": True, "reason": None}
	maximum = frappe.get_cached_doc("Vehicle Category", maximum_vehicle_category)
	maximum_weight = convert_measurement(
		maximum.max_payload_weight,
		maximum.max_payload_weight_uom,
		category.max_payload_weight_uom,
		_("maximum access vehicle payload"),
	)
	maximum_volume = convert_measurement(
		maximum.max_volume,
		maximum.max_volume_uom,
		category.max_volume_uom,
		_("maximum access vehicle volume"),
	)
	compatible = (
		(not maximum_weight or flt(category.max_payload_weight) <= flt(maximum_weight))
		and (not maximum_volume or flt(category.max_volume) <= flt(maximum_volume))
	)
	return {
		"compatible": compatible,
		"reason": None
		if compatible
		else _("Delivery location allows vehicles no larger than {0}.").format(
			maximum.vehicle_category_name or maximum.name
		),
	}


def maximum_fitting_fraction(totals, category):
	"""Return the largest common order fraction that fits all configured dimensions."""
	ratios = []
	for dimension in CAPACITY_DIMENSIONS:
		required = flt(totals.get(dimension))
		capacity = flt(category.capacities.get(dimension))
		if required and capacity:
			ratios.append(capacity / required)
	return min([1, *ratios]) if ratios else 1


def get_available_vehicle_category_counts(company, dispatch_date):
	"""Count enabled physical vehicles, subtracting active allocations on the date."""
	vehicles = frappe.get_all(
		"Vehicle",
		filters={
			"vuna_transport_enabled": 1,
			"vuna_vehicle_category": ["is", "set"],
			"company": ["in", [company, ""]],
		},
		fields=["name", "vuna_vehicle_category"],
	)
	counts = Counter(vehicle.vuna_vehicle_category for vehicle in vehicles)
	vehicle_categories = {vehicle.name: vehicle.vuna_vehicle_category for vehicle in vehicles}
	if not vehicle_categories:
		return {"counts": {}, "fleet_tracked": False}

	allocations = frappe.get_all(
		"Vehicle Allocation",
		filters={
			"vehicle": ["in", list(vehicle_categories)],
			"docstatus": 1,
			"status": ["in", ["Allocated", "Accepted"]],
		},
		fields=["vehicle", "delivery_load"],
	)
	load_dates = dict(
		frappe.get_all(
			"Delivery Load",
			filters={"name": ["in", [row.delivery_load for row in allocations]]},
			fields=["name", "dispatch_date"],
			as_list=True,
		)
	) if allocations else {}
	for allocation in allocations:
		if getdate(load_dates.get(allocation.delivery_load)) == getdate(dispatch_date):
			category = vehicle_categories.get(allocation.vehicle)
			counts[category] = max(counts[category] - 1, 0)
	return {"counts": dict(counts), "fleet_tracked": True}


def _normalise_equipment(value):
	return {
		item.strip().lower()
		for item in (value or "").replace("\n", ",").split(",")
		if item.strip()
	}
