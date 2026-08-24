"""Validation and master-data synchronization for ERPNext Vehicles."""

import frappe
from frappe import _

from vuna_transport.services.security_service import require_document_permission
from vuna_transport.services.vehicle_availability_service import get_vehicle_status


def validate_vehicle(doc, method=None):
	"""Populate standard Vehicle fields from the selected Vuna Vehicle Model."""
	if not getattr(doc, "vuna_vehicle_model", None):
		return

	vehicle_model = frappe.get_cached_doc("Vehicle Model", doc.vuna_vehicle_model)
	if vehicle_model.disabled:
		frappe.throw(_("Vehicle Model {0} is disabled.").format(vehicle_model.name))

	doc.make = vehicle_model.manufacturer
	doc.model = vehicle_model.model_name
	doc.vuna_vehicle_category = vehicle_model.vehicle_category
	if vehicle_model.fuel_type and vehicle_model.fuel_type in (
		"Petrol",
		"Diesel",
		"Natural Gas",
		"Electric",
	):
		doc.fuel_type = vehicle_model.fuel_type


@frappe.whitelist()
def get_vehicle_operational_status(vehicle):
	"""Return the current derived status for one readable standard Vehicle."""
	doc = frappe.get_doc("Vehicle", vehicle)
	require_document_permission(doc, "read")
	return get_vehicle_status(doc.name)
