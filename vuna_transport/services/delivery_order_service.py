"""Services for creating and maintaining transport demand."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, getdate

from vuna_transport.services.measurement_service import convert_measurement, get_transport_uoms
from vuna_transport.services.location_service import get_address_planning_data
from vuna_transport.services.delivery_method_service import canonical_delivery_method


GROUPING_FIELDS = (
	"delivery_date",
	"delivery_address",
	"source_warehouse",
	"transport_zone",
	"preferred_route",
	"handling_profile",
	"delivery_method",
)


def create_delivery_orders_for_sales_order(sales_order):
	"""Create Delivery Orders for eligible Sales Order items.

	Items are grouped by delivery date, delivery address, source warehouse, zone,
	and route. Existing Delivery Order Item links are respected, so the operation is
	idempotent and safe to call from a Sales Order hook.

	Args:
		sales_order: Sales Order document or document name.

	Returns:
		list[str]: Names of newly-created Delivery Orders.
	"""
	doc = _get_sales_order(sales_order)
	_validate_sales_order(doc)
	if not _requires_transport_planning(doc):
		return []

	groups = defaultdict(list)
	for item in doc.items:
		transport_qty = _get_transport_quantity(item)
		if transport_qty <= 0 or _is_item_already_linked(item.name):
			continue

		group_key = _get_group_key(doc, item)
		if not group_key[1]:
			_log_non_delivery_item(doc, item)
			continue
		groups[group_key].append((item, transport_qty))

	created = []
	for group_key, group_items in groups.items():
		created.append(_create_delivery_order(doc, group_key, group_items))

	return created


def _get_sales_order(sales_order):
	if isinstance(sales_order, str):
		return frappe.get_doc("Sales Order", sales_order)
	return sales_order


def _validate_sales_order(sales_order):
	if sales_order.doctype != "Sales Order":
		frappe.throw(_("Delivery Orders can only be generated from a Sales Order."))

	if sales_order.docstatus != 1:
		frappe.throw(_("The Sales Order must be submitted before Delivery Orders are generated."))

	settings = frappe.get_single("Transport Settings")
	if not settings.enabled:
		frappe.throw(_("Transport Management is disabled in Transport Settings."))


def _get_transport_quantity(item):
	ordered_qty = flt(getattr(item, "qty", 0))
	delivered_qty = flt(getattr(item, "delivered_qty", 0))
	return max(ordered_qty - delivered_qty, 0)


def _get_group_key(sales_order, item):
	delivery_address = getattr(item, "customer_address", None) or getattr(
		sales_order, "customer_address", None
	) or getattr(sales_order, "shipping_address_name", None)
	address_data = get_address_planning_data(delivery_address)
	values = {
		"delivery_date": getattr(item, "delivery_date", None) or getattr(sales_order, "delivery_date", None),
		"delivery_address": delivery_address,
		"source_warehouse": getattr(item, "warehouse", None),
		"transport_zone": getattr(item, "transport_zone", None) or address_data.delivery_zone,
		"preferred_route": getattr(item, "preferred_route", None),
		"handling_profile": getattr(item, "transport_handling_profile", None),
		"delivery_method": canonical_delivery_method(
			getattr(sales_order, "vuna_delivery_method", None)
		),
	}

	return tuple(_normalise_group_value(values[field]) for field in GROUPING_FIELDS)


def _normalise_group_value(value):
	if value is None:
		return None
	if hasattr(value, "isoformat"):
		return getdate(value).isoformat()
	return value


def _is_item_already_linked(sales_order_item):
	return bool(
		frappe.get_all(
			"Delivery Order Item",
			filters={"sales_order_item": sales_order_item},
			limit=1,
		)
	)


def _create_delivery_order(sales_order, group_key, group_items):
	(
		delivery_date,
		delivery_address,
		source_warehouse,
		transport_zone,
		preferred_route,
		handling_profile,
		delivery_method,
	) = group_key
	_validate_group_key(delivery_date, source_warehouse)
	delivery_order = frappe.new_doc("Delivery Order")

	delivery_order.company = sales_order.company
	delivery_order.source_document_type = "Sales Order"
	delivery_order.source_document = sales_order.name
	delivery_order.sales_order = sales_order.name
	delivery_order.customer = sales_order.customer
	delivery_order.customer_name = sales_order.customer_name
	delivery_order.delivery_address = delivery_address
	delivery_order.required_delivery_date = delivery_date
	delivery_order.source_warehouse = source_warehouse
	delivery_order.transport_zone = transport_zone
	delivery_order.preferred_route = preferred_route
	delivery_order.transport_handling_profile = handling_profile
	delivery_order.delivery_method = delivery_method
	delivery_order.status = "Open"
	address_data = get_address_planning_data(delivery_address)
	requirements = _get_group_transport_requirements(group_items, handling_profile)
	delivery_order.delivery_from_time = address_data.delivery_from_time
	delivery_order.delivery_to_time = address_data.delivery_to_time
	delivery_order.average_service_duration = address_data.average_service_duration
	delivery_order.maximum_vehicle_category = address_data.maximum_vehicle_category
	delivery_order.restricted_access = address_data.restricted_access
	delivery_order.loading_dock_available = address_data.loading_dock_available
	delivery_order.access_notes = address_data.access_notes
	delivery_order.refrigeration_required = requirements.refrigeration_required
	delivery_order.tail_lift_required = bool(
		requirements.tail_lift_required or address_data.requires_tail_lift
	)
	delivery_order.required_body_type = requirements.required_body_type
	delivery_order.special_equipment_required = requirements.special_equipment_required
	uoms = get_transport_uoms()
	delivery_order.weight_uom = uoms.weight_uom
	delivery_order.volume_uom = uoms.volume_uom

	totals = defaultdict(float)
	for item, transport_qty in group_items:
		row_values = _get_delivery_order_item_values(item, transport_qty)
		delivery_order.append("items", row_values)
		for field in ("transport_qty", "weight", "volume", "pallets", "cases", "crates"):
			totals[field] += flt(row_values.get(field))

	delivery_order.total_quantity = totals["transport_qty"]
	delivery_order.requested_quantity = totals["transport_qty"]
	delivery_order.allocated_quantity = 0
	delivery_order.reserved_quantity = 0
	delivery_order.unallocated_quantity = totals["transport_qty"]
	delivery_order.total_weight = totals["weight"]
	delivery_order.total_volume = totals["volume"]
	delivery_order.total_pallets = totals["pallets"]
	delivery_order.total_cases = totals["cases"]
	delivery_order.total_crates = totals["crates"]

	delivery_order.insert(ignore_permissions=True)
	return delivery_order.name


def _get_group_transport_requirements(group_items, handling_profile=None):
	items = [frappe.get_cached_doc("Item", row.item_code) for row, _quantity in group_items]
	body_types = {
		item.vuna_required_body_type
		for item in items
		if getattr(item, "vuna_required_body_type", None)
	}
	if len(body_types) > 1:
		frappe.throw(
			_("Items in one Delivery Order require conflicting vehicle body types: {0}.").format(
				", ".join(sorted(body_types))
			)
		)
	equipment = sorted(
		{
			value.strip()
			for item in items
			for value in (getattr(item, "vuna_special_equipment_required", None) or "").replace(
				"\n", ","
			).split(",")
			if value.strip()
		}
	)
	profile = (handling_profile or "").lower()
	return frappe._dict(
		refrigeration_required=any(
			getattr(item, "vuna_refrigeration_required", 0) for item in items
		) or any(keyword in profile for keyword in ("refriger", "cold chain", "chilled", "frozen")),
		tail_lift_required=any(
			getattr(item, "vuna_tail_lift_required", 0) for item in items
		) or "tail lift" in profile,
		required_body_type=next(iter(body_types), None),
		special_equipment_required=", ".join(equipment),
	)


def _requires_transport_planning(sales_order):
	delivery_method = getattr(sales_order, "vuna_delivery_method", None)
	if not delivery_method:
		return True
	method = frappe.get_cached_doc("Delivery Method", delivery_method)
	if method.disabled:
		frappe.throw(_("Delivery Method {0} is disabled.").format(method.name))
	return bool(method.requires_transport_planning)


def _validate_group_key(delivery_date, source_warehouse):
	missing = []
	if not delivery_date:
		missing.append("delivery date")
	if not source_warehouse:
		missing.append("source warehouse")

	if missing:
		frappe.throw(
			_("Delivery Order cannot be created because the following values are missing: {0}.").format(
				", ".join(missing)
			)
		)


def _log_non_delivery_item(sales_order, item):
	frappe.logger("vuna_transport").info(
		"Skipped Delivery Order for Sales Order %s item %s because no delivery address was provided.",
		sales_order.name,
		item.name,
	)


def _get_delivery_order_item_values(item, transport_qty):
	measurements = _get_item_transport_measurements(item, transport_qty)
	values = {
		"sales_order_item": item.name,
		"item_code": item.item_code,
		"item_name": getattr(item, "item_name", None),
		"ordered_qty": flt(getattr(item, "qty", 0)),
		"transport_qty": transport_qty,
		"stock_uom": getattr(item, "stock_uom", None) or getattr(item, "uom", None),
		"s_warehouse": getattr(item, "warehouse", None),
		"delivery_date": getattr(item, "delivery_date", None),
		"weight": measurements.weight,
		"weight_uom": measurements.weight_uom,
		"volume": measurements.volume,
		"volume_uom": measurements.volume_uom,
		"pallets": _get_item_measure(item, "pallets", transport_qty),
		"cases": _get_item_measure(item, "cases", transport_qty),
		"crates": _get_item_measure(item, "crates", transport_qty),
		"transport_handling_profile": getattr(item, "transport_handling_profile", None),
	}
	return values


def _get_item_transport_measurements(sales_order_item, transport_qty):
	item = frappe.get_cached_doc("Item", sales_order_item.item_code)
	uoms = get_transport_uoms()
	stock_quantity = flt(transport_qty) * flt(getattr(sales_order_item, "conversion_factor", 1) or 1)
	if flt(item.weight_per_unit) and not uoms.weight_uom:
		frappe.throw(_("Set Default Weight UOM in Transport Settings."))
	if flt(getattr(item, "vuna_volume_per_stock_uom", 0)) and not uoms.volume_uom:
		frappe.throw(_("Set Default Volume UOM in Transport Settings."))

	weight = convert_measurement(
		flt(item.weight_per_unit) * stock_quantity,
		item.weight_uom,
		uoms.weight_uom or item.weight_uom,
		_("item weight"),
	)
	volume_uom = getattr(item, "vuna_volume_uom", None)
	volume = convert_measurement(
		flt(getattr(item, "vuna_volume_per_stock_uom", 0)) * stock_quantity,
		volume_uom,
		uoms.volume_uom or volume_uom,
		_("item volume"),
	)

	return frappe._dict(
		weight=weight,
		weight_uom=uoms.weight_uom or item.weight_uom,
		volume=volume,
		volume_uom=uoms.volume_uom or volume_uom,
	)


def _get_item_measure(item, measure, quantity):
	value = getattr(item, measure, None)
	if value is not None:
		return flt(value)

	per_unit = getattr(item, f"{measure}_per_unit", None)
	return flt(per_unit) * quantity if per_unit is not None else 0
