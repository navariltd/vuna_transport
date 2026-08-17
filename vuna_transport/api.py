import frappe

from vuna_transport.services.security_service import require_document_permission
from vuna_transport.services.delivery_load_service import get_compatible_vehicle_category_names


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_vehicles_for_load(doctype, txt, searchfield, start, page_len, filters):
	load = frappe.get_doc("Delivery Load", filters.get("delivery_load"))
	require_document_permission(load, "read")
	vehicle_filters = {"docstatus": ["<", 2], "vuna_transport_enabled": 1}
	if load.company:
		vehicle_filters["company"] = ["in", (load.company, "", None)]
	compatible_categories = get_compatible_vehicle_category_names(load)
	if compatible_categories:
		vehicle_filters["vuna_vehicle_category"] = ["in", compatible_categories]
	else:
		return []
	return frappe.get_list(
		"Vehicle",
		filters=vehicle_filters,
		or_filters={"name": ["like", f"%{txt}%"], "license_plate": ["like", f"%{txt}%"]},
		fields=["name", "license_plate", "make", "model"],
		limit_start=start,
		limit_page_length=page_len,
		as_list=True,
	)
