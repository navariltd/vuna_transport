import frappe

from vuna_transport.services.fleet.driver_availability_service import (
	get_driver_availability_map,
)
from vuna_transport.services.fleet.vehicle_allocation_service import (
	get_allocation_resource_window,
	get_load_resource_window,
	validate_allocation_load_set,
)
from vuna_transport.services.fleet.vehicle_availability_service import (
	get_vehicle_availability_map,
)
from vuna_transport.services.planning.delivery_load_service import get_compatible_vehicle_category_names
from vuna_transport.services.shared.security_service import require_document_permission


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_vehicles_for_load(doctype, txt, searchfield, start, page_len, filters):
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	load = frappe.get_doc("Delivery Load", filters.get("delivery_load"))
	require_document_permission(load, "read")
	if filters.get("company") and filters.get("company") != load.company:
		return []
	if (
		filters.get("required_vehicle_category")
		and filters.get("required_vehicle_category") != load.required_vehicle_category
	):
		return []
	from_time, to_time = get_load_resource_window(load)
	vehicle_filters = {"docstatus": ["<", 2], "vuna_transport_enabled": 1}
	if (filters or {}).get("allocation_type") == "Subcontractor":
		vehicle_filters.pop("vuna_transport_enabled", None)
	if load.company:
		vehicle_filters["company"] = ["in", (load.company, "", None)]
	compatible_categories = get_compatible_vehicle_category_names(load)
	if load.required_vehicle_category:
		compatible_categories = {load.required_vehicle_category}
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
		if result.dispatch_eligible and result.vehicle_category in compatible_categories
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
	available_names = [result.driver for result in availability.values() if result.dispatch_eligible]
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


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_delivery_loads_for_allocation(doctype, txt, searchfield, start, page_len, filters):
	"""Offer only operational loads compatible with the current allocation composition."""
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	selected = filters.get("selected_delivery_loads") or []
	if isinstance(selected, str):
		selected = frappe.parse_json(selected) or []
	company = filters.get("company")
	vehicle_category = filters.get("required_vehicle_category")
	vehicle_name = filters.get("vehicle")
	if vehicle_name:
		vehicle_category = frappe.db.get_value("Vehicle", vehicle_name, "vuna_vehicle_category") or vehicle_category
	load_filters = {"docstatus": 1, "status": ["not in", ["Completed", "Completed with Exceptions", "Cancelled"]]}
	if company:
		load_filters["company"] = company
	if vehicle_category:
		load_filters["required_vehicle_category"] = vehicle_category
	candidates = frappe.get_all(
		"Delivery Load",
		filters=load_filters,
		or_filters={"name": ["like", f"%{txt}%"]},
		fields=["name", "company", "required_vehicle_category", "active_vehicle_allocation"],
		order_by="dispatch_date asc, creation asc",
		limit_page_length=min(max(int(start or 0) + int(page_len or 20) + 50, 100), 300),
	)
	results = []
	for candidate in candidates:
		if candidate.name in selected:
			continue
		if candidate.active_vehicle_allocation:
			continue
		try:
			loads = validate_allocation_load_set([*selected, candidate.name], vehicle_category)
			for load in loads:
				require_document_permission(load, "read")
		except frappe.ValidationError:
			continue
		results.append((candidate.name, candidate.name))
	return results[int(start or 0) : int(start or 0) + int(page_len or 20)]


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_vehicles_for_allocation(doctype, txt, searchfield, start, page_len, filters):
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	load_names = filters.get("delivery_loads") or []
	if isinstance(load_names, str):
		load_names = frappe.parse_json(load_names) or []
	if not load_names:
		return []
	loads = validate_allocation_load_set(load_names, filters.get("required_vehicle_category"))
	for load in loads:
		require_document_permission(load, "read")
	from_time, to_time = get_allocation_resource_window(loads)
	category = filters.get("required_vehicle_category") or loads[0].required_vehicle_category
	availability = get_vehicle_availability_map(
		company=loads[0].company,
		vehicle_category=category,
		from_time=from_time,
		to_time=to_time,
	)
	available = [row.vehicle for row in availability.values() if row.dispatch_eligible]
	if not available:
		return []
	vehicle_filters = {"name": ["in", available]}
	if filters.get("allocation_type") != "Subcontractor":
		vehicle_filters["vuna_transport_enabled"] = 1
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
def get_drivers_for_allocation(doctype, txt, searchfield, start, page_len, filters):
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	load_names = filters.get("delivery_loads") or []
	if isinstance(load_names, str):
		load_names = frappe.parse_json(load_names) or []
	if not load_names:
		return []
	loads = validate_allocation_load_set(load_names)
	for load in loads:
		require_document_permission(load, "read")
	from_time, to_time = get_allocation_resource_window(loads)
	driver_names = frappe.get_list("Driver", filters={"status": "Active"}, pluck="name", limit_page_length=1000)
	availability = get_driver_availability_map(
		driver_names=driver_names,
		from_time=from_time,
		to_time=to_time,
		company=loads[0].company,
	)
	available = [row.driver for row in availability.values() if row.dispatch_eligible]
	if not available:
		return []
	return frappe.get_list(
		"Driver",
		filters={"name": ["in", available], "status": "Active"},
		or_filters={"name": ["like", f"%{txt}%"], "full_name": ["like", f"%{txt}%"]},
		fields=["name", "full_name", "cell_number", "license_number"],
		limit_start=start,
		limit_page_length=page_len,
		as_list=True,
	)
