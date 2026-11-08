"""Explicit customer-planning field contract for the Delivery Order cutover.

Keep canonical Transport Demand quantities/UOMs and endpoints. Do not introduce
another transport_qty/uom/delivery_address/required_delivery_date truth.
"""

ROOT_FIELDS = (
	"sales_order",
	"customer_name",
	"territory",
	"county",
	"constituency",
	"town",
	"source_address",
	"delivery_method",
	"preferred_route",
	"priority",
	"delivery_from_time",
	"delivery_to_time",
	"transport_handling_profile",
	"transport_compatibility_group",
	"delivery_group",
	"refrigeration_required",
	"tail_lift_required",
	"required_body_type",
	"special_equipment_required",
	"maximum_vehicle_category",
	"restricted_access",
	"loading_dock_available",
	"average_service_duration",
	"access_notes",
	"source_location",
	"source_latitude",
	"source_longitude",
	"destination_location",
	"destination_latitude",
	"destination_longitude",
	"total_weight",
	"weight_uom",
	"total_volume",
	"volume_uom",
	"total_pallets",
	"total_cases",
	"total_crates",
)

ITEM_FIELDS = (
	"sales_order_item",
	"item_name",
	"ordered_qty",
	"s_warehouse",
	"delivery_date",
	"transport_handling_profile",
	"volume",
	"volume_uom",
	"pallets",
	"cases",
	"crates",
)

# Existing target columns are deliberately reused instead of copied twice.
ROOT_ALIASES = {"delivery_address": "destination_address", "required_delivery_date": "required_date"}
ITEM_ALIASES = {"transport_qty": "transaction_qty", "uom": "transaction_uom"}

CUSTOMER_EXECUTION_STATUSES = ("Dispatched", "Partially Delivered", "Delivered", "Closed")


def customer_fields(source, fields):
	"""Copy known fields only; custom columns remain in the recovery snapshot."""
	return {field: source.get(field) for field in fields}


def enrich_customer_demand(demand, source):
	"""Populate the customer cockpit while the source still identifies a legacy DO.

	No stock/accounting actions, state transitions or source edits. Source quantities
	and endpoints are validated by the normal source adapter before this is called.
	"""
	demand.update(customer_fields(source, ROOT_FIELDS))
	by_source = {row.name: row for row in source.get("items")}
	for row in demand.get("items"):
		original = by_source[row.source_item]
		row.update(customer_fields(original, ITEM_FIELDS))
		# These are transport snapshots, not recalculated using today's Item setup.
		for field in ("weight", "weight_uom", "delivery_group", "transport_compatibility_group"):
			row.set(field, original.get(field))
