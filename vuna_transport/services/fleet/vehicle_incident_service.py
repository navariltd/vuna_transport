"""Operational Vehicle Incident validation and integration services."""

import frappe
from frappe import _

from vuna_transport.services.fleet.vehicle_allocation_service import (
	allocation_includes_load,
	get_allocation_load_names,
	make_replacement_allocation,
)
from vuna_transport.services.execution.lineage_service import get_delivery_trip_load_names
from vuna_transport.services.shared.security_service import (
	require_doctype_permission,
	require_document_permission,
)

RESOLVED_STATUSES = ("Resolved", "Closed")


def validate_incident(incident):
	vehicle = frappe.db.get_value(
		"Vehicle",
		incident.vehicle,
		["name", "company", "vuna_asset"],
		as_dict=True,
	)
	if not vehicle:
		frappe.throw(_("Vehicle {0} does not exist.").format(incident.vehicle))

	if incident.company and vehicle.company and incident.company != vehicle.company:
		frappe.throw(_("Vehicle {0} belongs to Company {1}.").format(incident.vehicle, vehicle.company))
	incident.company = vehicle.company or incident.company

	allocation = _get_optional_doc("Vehicle Allocation", incident.vehicle_allocation)
	trip = _get_optional_doc("Delivery Trip", incident.delivery_trip)
	allocation = _load_context_from_references(incident, allocation, trip)
	load = _get_optional_doc("Delivery Load", incident.delivery_load)
	replacement = _get_optional_doc("Vehicle Allocation", incident.replacement_vehicle_allocation)
	repair = _get_optional_doc("Asset Repair", incident.asset_repair)
	_validate_incident_context(
		incident,
		vehicle,
		load=load,
		trip=trip,
		allocation=allocation,
		replacement=replacement,
		repair=repair,
	)
	_validate_resolution(incident)


def _load_context_from_references(incident, allocation=None, trip=None):
	if trip:
		trip_loads = get_delivery_trip_load_names(trip)
		if not incident.delivery_load and len(trip_loads) == 1:
			incident.delivery_load = trip_loads[0]
		incident.vehicle_allocation = incident.vehicle_allocation or (
			getattr(trip, "vuna_current_vehicle_allocation", None)
			or getattr(trip, "vuna_vehicle_allocation", None)
		)
		if not allocation and incident.vehicle_allocation:
			allocation = frappe.get_doc("Vehicle Allocation", incident.vehicle_allocation)
	if allocation:
		incident.delivery_load = incident.delivery_load or next(iter(get_allocation_load_names(allocation)), None)
		incident.driver = incident.driver or allocation.driver
	return allocation


def _validate_incident_context(
	incident,
	vehicle,
	*,
	load=None,
	trip=None,
	allocation=None,
	replacement=None,
	repair=None,
):
	for reference, label in (
		(load, _("Delivery Load")),
		(trip, _("Delivery Trip")),
		(allocation, _("Vehicle Allocation")),
	):
		if reference and getattr(reference, "company", None) and (reference.company != incident.company):
			frappe.throw(_("{0} {1} belongs to a different Company.").format(label, reference.name))

	if trip:
		if incident.delivery_load and incident.delivery_load not in get_delivery_trip_load_names(trip):
			frappe.throw(_("The Delivery Trip does not belong to the selected Delivery Load."))
		if allocation and allocation.name not in {
			getattr(trip, "vuna_vehicle_allocation", None),
			getattr(trip, "vuna_current_vehicle_allocation", None),
		}:
			frappe.throw(_("The Vehicle Allocation is not linked to the selected Delivery Trip."))

	if allocation:
		if incident.delivery_load and not allocation_includes_load(allocation, incident.delivery_load):
			frappe.throw(_("The Vehicle Allocation belongs to a different Delivery Load."))
		if not allocation.vehicle:
			frappe.throw(_("The Vehicle Allocation does not reference a physical ERPNext Vehicle."))
		if allocation.vehicle and allocation.vehicle != incident.vehicle:
			frappe.throw(_("The Vehicle Allocation belongs to a different Vehicle."))
		if incident.driver and allocation.driver and allocation.driver != incident.driver:
			frappe.throw(_("The selected Driver does not match the Vehicle Allocation."))
	elif trip:
		trip_vehicle = getattr(trip, "vuna_current_vehicle", None) or getattr(trip, "vehicle", None)
		trip_driver = getattr(trip, "vuna_current_driver", None) or getattr(trip, "driver", None)
		if trip_vehicle and trip_vehicle != incident.vehicle:
			frappe.throw(_("The selected Vehicle does not match the Delivery Trip."))
		if incident.driver and trip_driver and trip_driver != incident.driver:
			frappe.throw(_("The selected Driver does not match the Delivery Trip."))

	if load and load.company and load.company != incident.company:
		frappe.throw(_("The Delivery Load belongs to a different Company."))

	if replacement:
		_validate_replacement_context(incident, replacement)
	if repair:
		_validate_asset_repair_context(incident, vehicle, repair)


def _validate_replacement_context(incident, replacement):
	if incident.delivery_load and not allocation_includes_load(replacement, incident.delivery_load):
		frappe.throw(_("The Replacement Allocation belongs to a different Delivery Load."))
	if replacement.assignment_event != "Reallocation":
		frappe.throw(_("The linked Vehicle Allocation is not a replacement allocation."))
	if incident.vehicle_allocation and replacement.replacement_of != incident.vehicle_allocation:
		frappe.throw(_("The Replacement Allocation does not replace the incident allocation."))
	if getattr(replacement, "vehicle_incident", None) and replacement.vehicle_incident != incident.name:
		frappe.throw(_("The Replacement Allocation belongs to another Vehicle Incident."))


def _validate_asset_repair_context(incident, vehicle, repair):
	if not vehicle.vuna_asset:
		frappe.throw(_("Vehicle {0} is not linked to an ERPNext Asset.").format(vehicle.name))
	if repair.asset != vehicle.vuna_asset:
		frappe.throw(_("Asset Repair {0} belongs to a different Asset.").format(repair.name))


def _validate_resolution(incident):
	if incident.resolution_status not in RESOLVED_STATUSES:
		return
	if not incident.resolved_on:
		frappe.throw(_("Resolved On is required for a resolved or closed incident."))


def validate_incident_submission(incident):
	"""Require every incident to be resolved before it is frozen by submission."""
	if incident.resolution_status not in RESOLVED_STATUSES:
		frappe.throw(
			_(
				"Resolve the Vehicle Incident before submitting it. "
				"An incident must be Resolved or Closed before it can be submitted."
			)
		)


@frappe.whitelist()
def make_replacement_allocation_from_incident(source_name):
	incident = frappe.get_doc("Vehicle Incident", source_name)
	require_document_permission(incident, "write")
	if incident.resolution_status in RESOLVED_STATUSES:
		frappe.throw(_("A resolved or closed incident cannot initiate reallocation."))
	if not incident.replacement_required:
		frappe.throw(_("Mark Replacement Required before initiating reallocation."))
	if not incident.delivery_load or not incident.vehicle_allocation:
		frappe.throw(_("Link the Delivery Load and affected Vehicle Allocation before reallocation."))
	if incident.replacement_vehicle_allocation:
		return frappe.get_doc("Vehicle Allocation", incident.replacement_vehicle_allocation)

	allocation = make_replacement_allocation(incident.delivery_load)
	if allocation.replacement_of != incident.vehicle_allocation:
		frappe.throw(
			_(
				"The active Vehicle Allocation has changed since this incident was recorded. Refresh the incident."
			)
		)
	if getattr(allocation, "vehicle_incident", None) and allocation.vehicle_incident != incident.name:
		frappe.throw(
			_("The draft Replacement Allocation already belongs to Vehicle Incident {0}.").format(
				allocation.vehicle_incident
			)
		)
	allocation.vehicle_incident = incident.name
	allocation.replacement_reason = (
		incident.incident_type
		if incident.incident_type in ("Breakdown", "Accident")
		else "Vehicle Unavailable"
	)
	allocation.replacement_details = incident.description
	return allocation


def validate_replacement_allocation_incident(allocation):
	if not getattr(allocation, "vehicle_incident", None):
		return
	incident = frappe.get_doc("Vehicle Incident", allocation.vehicle_incident)
	if not incident.replacement_required:
		frappe.throw(_("Vehicle Incident {0} does not require replacement.").format(incident.name))
	_validate_replacement_context(incident, allocation)


def link_replacement_allocation_to_incident(allocation):
	if not getattr(allocation, "vehicle_incident", None):
		return
	incident = frappe.get_doc("Vehicle Incident", allocation.vehicle_incident)
	require_document_permission(incident, "write")
	_validate_replacement_context(incident, allocation)
	if incident.replacement_vehicle_allocation and incident.replacement_vehicle_allocation != allocation.name:
		frappe.throw(
			_("Vehicle Incident {0} already has Replacement Allocation {1}.").format(
				incident.name, incident.replacement_vehicle_allocation
			)
		)
	incident.db_set("replacement_vehicle_allocation", allocation.name, update_modified=True)


@frappe.whitelist()
def create_asset_repair(source_name):
	incident = frappe.get_doc("Vehicle Incident", source_name)
	require_document_permission(incident, "write")
	if incident.asset_repair:
		return incident.asset_repair
	require_doctype_permission("Asset Repair", "create")
	vehicle = frappe.get_doc("Vehicle", incident.vehicle)
	require_document_permission(vehicle, "read")
	if not vehicle.vuna_asset:
		frappe.throw(_("Vehicle {0} is not linked to an ERPNext Asset.").format(vehicle.name))
	asset = frappe.get_doc("Asset", vehicle.vuna_asset)
	require_document_permission(asset, "read")

	repair = frappe.new_doc("Asset Repair")
	repair.asset = asset.name
	repair.failure_date = incident.incident_datetime
	repair.repair_status = "Pending"
	repair.description = _("Vehicle Incident {0}: {1}").format(incident.name, incident.description)
	repair.insert()
	incident.db_set("asset_repair", repair.name, update_modified=True)
	repair.add_comment("Info", _("Created from Vehicle Incident {0}.").format(incident.name))
	return repair.name


def _get_optional_doc(doctype, name):
	return frappe.get_doc(doctype, name) if name else None
