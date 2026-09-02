import frappe

from vuna_transport.services.shared.security_service import require_document_permission
from vuna_transport.services.planning.delivery_load_service import get_compatible_vehicle_category_names
from vuna_transport.services.fleet.driver_availability_service import (
	get_driver_availability_map,
)
from vuna_transport.services.fleet.vehicle_availability_service import (
	get_vehicle_availability_map,
)
from vuna_transport.services.fleet.vehicle_allocation_service import get_load_resource_window


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_vehicles_for_load(doctype, txt, searchfield, start, page_len, filters):
	load = frappe.get_doc("Delivery Load", filters.get("delivery_load"))
	require_document_permission(load, "read")
	from_time, to_time = get_load_resource_window(load)
	vehicle_filters = {"docstatus": ["<", 2], "vuna_transport_enabled": 1}
	if (filters or {}).get("allocation_type") == "Subcontractor":
		vehicle_filters.pop("vuna_transport_enabled", None)
	if load.company:
		vehicle_filters["company"] = ["in", (load.company, "", None)]
	compatible_categories = get_compatible_vehicle_category_names(load)
	if compatible_categories:
		vehicle_filters["vuna_vehicle_category"] = ["in", compatible_categories]
	else:
		return []
	availability = get_vehicle_availability_map(
		company=load.company,
		from_time=from_time,
		to_time=to_time,
		delivery_load=load,
	)
	available_names = {
		result.vehicle
		for result in availability.values()
		if result.dispatch_eligible
		and result.vehicle_category in compatible_categories
	}
	if not available_names:
		return []
	vehicle_filters["name"] = ["in", list(available_names)]
	return frappe.get_list(
		"Vehicle",
		filters=vehicle_filters,
		or_filters={"name": ["like", f"%{txt}%"], "license_plate": ["like", f"%{txt}%"]},
		fields=["name", "license_plate", "make", "model"],
		limit_start=start,
		limit_page_length=page_len,
		as_list=True,
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_drivers_for_load(doctype, txt, searchfield, start, page_len, filters):
	load = frappe.get_doc("Delivery Load", filters.get("delivery_load"))
	require_document_permission(load, "read")
	from_time, to_time = get_load_resource_window(load)
	driver_names = frappe.get_list(
		"Driver",
		filters={"status": "Active"},
		pluck="name",
		limit_page_length=1000,
	)
	availability = get_driver_availability_map(
		driver_names=driver_names,
		from_time=from_time,
		to_time=to_time,
		company=load.company,
		exclude_delivery_load=load.name,
	)
	available_names = [
		result.driver for result in availability.values() if result.dispatch_eligible
	]
	if not available_names:
		return []
	return frappe.get_list(
		"Driver",
		filters={"name": ["in", available_names], "status": "Active"},
		or_filters={
			"name": ["like", f"%{txt}%"],
			"full_name": ["like", f"%{txt}%"],
		},
		fields=["name", "full_name", "cell_number", "license_number"],
		limit_start=start,
		limit_page_length=page_len,
		as_list=True,
	)
