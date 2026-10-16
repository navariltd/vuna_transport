import frappe
from frappe.utils import add_to_date, now_datetime

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
from vuna_transport.services.mobile.mobile_device_service import (
	bootstrap_device,
	get_assigned_delivery_trips,
	get_current_driver_profile,
	get_delivery_note_details,
	get_delivery_trip_details,
	prepare_mobile_proof_of_delivery,
	submit_mobile_proof_of_delivery,
	upload_mobile_pod_attachment,
)
from vuna_transport.services.mobile.mobile_request_service import (
	create_mobile_expense_claim,
	create_mobile_material_request,
	get_mobile_request_details,
	get_mobile_request_setup,
	get_mobile_requests,
	search_mobile_material_request_items,
)
from vuna_transport.services.mobile.mobile_trip_execution_service import (
	get_mobile_trip_incidents,
	report_mobile_trip_incident,
	resolve_mobile_trip_incident,
	start_mobile_delivery_trip,
)
from vuna_transport.services.planning.delivery_load_service import get_compatible_vehicle_category_names
from vuna_transport.services.shared.security_service import require_document_permission
from vuna_transport.services.shared.workflow_policy_service import (
	ALLOCATION_AFTER_DELIVERY_NOTES,
	get_vehicle_allocation_timing,
)


@frappe.whitelist()
def bootstrap_mobile_device(
	installation_id,
	device_identifier=None,
	platform=None,
	manufacturer=None,
	model_name=None,
	os_name=None,
	os_version=None,
	app_version=None,
):
	return bootstrap_device(
		installation_id=installation_id,
		device_identifier=device_identifier,
		platform=platform,
		manufacturer=manufacturer,
		model_name=model_name,
		os_name=os_name,
		os_version=os_version,
		app_version=app_version,
	)


@frappe.whitelist()
def get_mobile_driver_profile(installation_id, device_identifier=None):
	return get_current_driver_profile(installation_id, device_identifier)


@frappe.whitelist()
def get_mobile_assigned_delivery_trips(installation_id, device_identifier=None):
	return get_assigned_delivery_trips(installation_id, device_identifier)


@frappe.whitelist()
def get_mobile_delivery_trip_details(installation_id, trip_name, device_identifier=None):
	return get_delivery_trip_details(installation_id, trip_name, device_identifier)


@frappe.whitelist()
def get_mobile_delivery_note_details(
	installation_id,
	trip_name,
	stop_name,
	device_identifier=None,
):
	return get_delivery_note_details(
		installation_id,
		trip_name,
		stop_name,
		device_identifier,
	)


@frappe.whitelist()
def start_mobile_trip(installation_id, trip_name, device_identifier=None):
	return start_mobile_delivery_trip(installation_id, trip_name, device_identifier)


@frappe.whitelist()
def get_mobile_incidents(installation_id, trip_name, device_identifier=None):
	return get_mobile_trip_incidents(installation_id, trip_name, device_identifier)


@frappe.whitelist()
def report_mobile_incident(
	installation_id,
	trip_name,
	payload,
	device_identifier=None,
):
	return report_mobile_trip_incident(
		installation_id,
		trip_name,
		payload,
		device_identifier,
	)


@frappe.whitelist()
def resolve_mobile_incident(
	installation_id,
	trip_name,
	incident_name,
	resolution,
	device_identifier=None,
):
	return resolve_mobile_trip_incident(
		installation_id,
		trip_name,
		incident_name,
		resolution,
		device_identifier,
	)


@frappe.whitelist()
def prepare_mobile_pod(installation_id, trip_name, stop_name, device_identifier=None):
	return prepare_mobile_proof_of_delivery(
		installation_id,
		trip_name,
		stop_name,
		device_identifier,
	)


@frappe.whitelist()
def submit_mobile_pod(
	installation_id,
	trip_name,
	stop_name,
	payload,
	device_identifier=None,
):
	return submit_mobile_proof_of_delivery(
		installation_id,
		trip_name,
		stop_name,
		payload,
		device_identifier,
	)


@frappe.whitelist()
def upload_mobile_pod_file(
	installation_id,
	trip_name,
	stop_name,
	pod_name,
	attachment_type,
	content,
	filename,
	device_identifier=None,
	content_encoding="base64",
):
	return upload_mobile_pod_attachment(
		installation_id,
		trip_name,
		stop_name,
		pod_name,
		attachment_type,
		content,
		filename,
		device_identifier,
		content_encoding,
	)


@frappe.whitelist()
def get_mobile_trip_request_setup(installation_id, trip_name, device_identifier=None):
	return get_mobile_request_setup(installation_id, trip_name, device_identifier)


@frappe.whitelist()
def get_mobile_request_history(installation_id, device_identifier=None, limit=50):
	return get_mobile_requests(installation_id, device_identifier, limit)


@frappe.whitelist()
def get_mobile_request(
	installation_id,
	doctype,
	name,
	device_identifier=None,
):
	return get_mobile_request_details(
		installation_id,
		doctype,
		name,
		device_identifier,
	)


@frappe.whitelist()
def search_mobile_material_items(
	installation_id,
	trip_name,
	txt=None,
	device_identifier=None,
):
	return search_mobile_material_request_items(
		installation_id,
		trip_name,
		txt,
		device_identifier,
	)


@frappe.whitelist()
def create_mobile_trip_expense_claim(
	installation_id,
	trip_name,
	payload,
	device_identifier=None,
):
	return create_mobile_expense_claim(
		installation_id,
		trip_name,
		payload,
		device_identifier,
	)


@frappe.whitelist()
def create_mobile_trip_material_request(
	installation_id,
	trip_name,
	payload,
	device_identifier=None,
):
	return create_mobile_material_request(
		installation_id,
		trip_name,
		payload,
		device_identifier,
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_vehicles_for_load(doctype, txt, searchfield, start, page_len, filters):
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	load_name = filters.get("delivery_load")
	if not load_name:
		return []
	load = frappe.get_doc("Delivery Load", load_name)
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
	load_name = filters.get("delivery_load")
	if not load_name:
		return []
	load = frappe.get_doc("Delivery Load", load_name)
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
def get_delivery_loads_for_allocation(
	doctype, txt, searchfield, start, page_len, filters, as_dict=False, **kwargs
):
	"""Offer only operational loads compatible with the current allocation composition."""
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	selected = filters.get("selected_delivery_loads") or []
	if isinstance(selected, str):
		selected = frappe.parse_json(selected) or []
	selected = [name for name in selected if name]
	company = filters.get("company")
	vehicle_category = filters.get("required_vehicle_category")
	vehicle_name = filters.get("vehicle")
	if vehicle_name:
		vehicle_category = (
			frappe.db.get_value("Vehicle", vehicle_name, "vuna_vehicle_category") or vehicle_category
		)
	if not vehicle_category:
		return []
	load_filters = {
		"docstatus": 1,
		"status": ["not in", ["Completed", "Completed with Exceptions", "Cancelled"]],
	}
	if company:
		load_filters["company"] = company
	if vehicle_category:
		load_filters["required_vehicle_category"] = vehicle_category
	candidates = frappe.get_all(
		"Delivery Load",
		filters=load_filters,
		or_filters={"name": ["like", f"%{txt}%"]},
		fields=[
			"name",
			"company",
			"required_vehicle_category",
			"active_vehicle_allocation",
			"delivery_method",
		],
		order_by="dispatch_date asc, creation asc",
		limit_page_length=min(max(int(start or 0) + int(page_len or 20) + 50, 100), 300),
	)
	candidates = _loads_at_vehicle_allocation_step(candidates)
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
		if as_dict:
			results.append(
				{
					"name": candidate.name,
					"company": candidate.company,
					"required_vehicle_category": candidate.required_vehicle_category,
				}
			)
		else:
			results.append((candidate.name, candidate.name))
	return results[int(start or 0) : int(start or 0) + int(page_len or 20)]


def _loads_at_vehicle_allocation_step(candidates):
	"""Keep only submitted loads whose current workflow action is vehicle allocation."""
	if not candidates:
		return []

	method_names = list({row.delivery_method for row in candidates if row.delivery_method}) or [""]
	methods = {
		row.name: bool(row.requires_vehicle_allocation)
		for row in frappe.get_all(
			"Delivery Method",
			filters={"name": ["in", method_names]},
			fields=["name", "requires_vehicle_allocation"],
		)
	}
	method_required = {row.name: methods.get(row.delivery_method, True) for row in candidates}
	eligible = [row for row in candidates if method_required[row.name]]
	if not eligible:
		return []

	# A draft allocation already owns the workflow step and should not be offered
	# as a second allocation candidate.
	allocation_rows = frappe.get_all(
		"Vehicle Allocation Load",
		filters={"delivery_load": ["in", [row.name for row in eligible]]},
		fields=["parent", "delivery_load"],
	)
	draft_allocations = set(
		frappe.get_all(
			"Vehicle Allocation",
			filters={
				"name": ["in", list({row.parent for row in allocation_rows}) or [""]],
				"docstatus": 0,
			},
			pluck="name",
		)
	)
	draft_loads = {row.delivery_load for row in allocation_rows if row.parent in draft_allocations}
	eligible = [row for row in eligible if row.name not in draft_loads]
	if get_vehicle_allocation_timing() != ALLOCATION_AFTER_DELIVERY_NOTES:
		return eligible

	# In the after-notes flow, the allocation step begins only after every
	# Delivery Order on the load has a submitted Delivery Note and no draft note.
	load_order_rows = frappe.get_all(
		"Delivery Load Order",
		filters={"parent": ["in", [row.name for row in eligible]]},
		fields=["parent", "delivery_order"],
	)
	expected = {}
	for row in load_order_rows:
		if row.delivery_order:
			expected.setdefault(row.parent, set()).add(row.delivery_order)

	notes = frappe.get_all(
		"Delivery Note",
		filters={"vuna_delivery_load": ["in", [row.name for row in eligible]]},
		fields=["vuna_delivery_load", "vuna_delivery_order", "docstatus"],
	)
	submitted = {}
	draft_loads = set()
	for note in notes:
		if note.docstatus == 0:
			draft_loads.add(note.vuna_delivery_load)
		elif note.docstatus == 1 and note.vuna_delivery_order:
			submitted.setdefault(note.vuna_delivery_load, set()).add(note.vuna_delivery_order)

	return [
		row
		for row in eligible
		if expected.get(row.name)
		and expected[row.name].issubset(submitted.get(row.name, set()))
		and row.name not in draft_loads
	]


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_vehicles_for_allocation(doctype, txt, searchfield, start, page_len, filters):
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	load_names = filters.get("delivery_loads") or []
	if isinstance(load_names, str):
		load_names = frappe.parse_json(load_names) or []
	load_names = [name for name in load_names if name]
	category = filters.get("required_vehicle_category")
	if not category:
		return []
	if load_names:
		loads = validate_allocation_load_set(load_names, category)
		for load in loads:
			require_document_permission(load, "read")
		from_time, to_time = get_allocation_resource_window(loads)
	else:
		now = now_datetime()
		from_time, to_time = now, add_to_date(now, seconds=1)
	availability = get_vehicle_availability_map(
		company=filters.get("company"),
		vehicle_category=category,
		from_time=from_time,
		to_time=to_time,
		delivery_load=loads[0] if load_names else None,
	)
	available = [row.vehicle for row in availability.values() if row.dispatch_eligible]
	if not available:
		return []
	vehicle_filters = {"name": ["in", available]}
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
	load_names = [name for name in load_names if name]
	if not load_names:
		return []
	loads = validate_allocation_load_set(load_names)
	for load in loads:
		require_document_permission(load, "read")
	from_time, to_time = get_allocation_resource_window(loads)
	driver_names = frappe.get_list(
		"Driver", filters={"status": "Active"}, pluck="name", limit_page_length=1000
	)
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
