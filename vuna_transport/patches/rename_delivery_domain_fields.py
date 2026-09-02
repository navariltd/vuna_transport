"""Copy existing values into the renamed delivery-domain fields after model sync."""

from frappe.model.utils.rename_field import rename_field

FIELD_RENAMES = (
	("Transport Settings", "auto_create_transport_requirements", "auto_create_delivery_orders"),
	("Delivery Load", "load_requirements", "delivery_orders"),
	("Delivery Load", "required_vehicle_type", "required_vehicle_category"),
	("Delivery Load Order", "transport_requirement", "delivery_order"),
	("Delivery Load Order", "transport_requirement_item", "delivery_order_item"),
	("Proof of Delivery", "transport_load", "delivery_load"),
	("Proof of Delivery", "transport_requirement", "delivery_order"),
	("Proof of Delivery Item", "transport_requirement_item", "delivery_order_item"),
	("Vehicle Category", "vehicle_type_name", "vehicle_category_name"),
	("Vehicle Category", "vehicle_category", "body_type"),
	("Vehicle Category", "maximum_payload_weight", "max_payload_weight"),
	("Vehicle Category", "maximum_payload_weight_uom", "max_payload_weight_uom"),
	("Vehicle Category", "maximum_volume", "max_volume"),
	("Vehicle Category", "maximum_volume_uom", "max_volume_uom"),
	("Vehicle Category", "maximum_pallets", "max_pallets"),
	("Vehicle Category", "maximum_cases", "max_cases"),
	("Vehicle Category", "maximum_crates", "max_crates"),
)


def execute():
	for doctype, old_fieldname, new_fieldname in FIELD_RENAMES:
		rename_field(doctype, old_fieldname, new_fieldname)
