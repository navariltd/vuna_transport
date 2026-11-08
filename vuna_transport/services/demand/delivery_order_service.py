"""Services for creating and maintaining transport demand."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, getdate

from vuna_transport.services.demand.delivery_method_service import (
	canonical_delivery_method,
	validate_sales_order_delivery_method,
)
from vuna_transport.services.routing.location_service import get_address_planning_data
from vuna_transport.services.shared.measurement_service import convert_measurement, get_transport_uoms
from vuna_transport.services.shared.security_service import (
	require_doctype_permission,
	require_document_permission,
)

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
	if not frappe.db.exists("DocType", "Delivery Order"):
		from vuna_transport.services.demand.sales_transport_demand_service import create_sales_demands

		return create_sales_demands(sales_order)
	doc = _get_sales_order(sales_order)
	_validate_sales_order(doc)
	if not _requires_transport_planning(doc):
		return []

	groups = defaultdict(list)
	for item in doc.items:
		# ERPNext Sales Orders can contain both stock and non-stock/service items.
		# Only stock items represent goods that Vuna can plan and dispatch.
		if not _is_stock_item(item):
			_log_non_stock_item(doc, item)
			continue

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


def sync_delivery_orders_for_sales_order(sales_order):
	"""Refresh existing Delivery Orders after a submitted Sales Order changes."""
	from vuna_transport.services.demand.source_policy_service import get_source_policy

	if not get_source_policy("Sales Order").enabled:
		return []
	doc = _get_sales_order(sales_order)
	_validate_sales_order(doc, check_generation_policy=False)
	from vuna_transport.services.demand.sales_transport_demand_service import refresh_sales_demands

	refresh_sales_demands(doc)
	if not frappe.db.exists("DocType", "Delivery Order"):
		from vuna_transport.services.demand.source_policy_service import automatically_create_from_source

		return automatically_create_from_source(doc)

	delivery_order_names = frappe.get_all("Delivery Order", filters={"sales_order": doc.name}, pluck="name")
	for delivery_order_name in delivery_order_names:
		delivery_order = frappe.get_doc("Delivery Order", delivery_order_name)
		sync_delivery_order_quantities(delivery_order, persist=True)
		sync_delivery_order_status_from_sales_order(delivery_order, doc)

	# The existing generator remains responsible for newly-added Sales Order Items.
	from vuna_transport.services.demand.source_policy_service import automatically_create_from_source

	return automatically_create_from_source(doc)


def sync_delivery_order_status_from_sales_order(delivery_order, sales_order=None):
	"""Reflect a closed source Sales Order on its still-open Delivery Order."""
	sales_order = sales_order or _get_sales_order(delivery_order.sales_order)
	if delivery_order.docstatus == 2:
		return
	if sales_order.docstatus == 2 or sales_order.status in ("Closed", "Cancelled"):
		if delivery_order.status not in ("Delivered", "Cancelled"):
			frappe.db.set_value(
				"Delivery Order", delivery_order.name, "status", "Closed", update_modified=False
			)
			delivery_order.status = "Closed"


@frappe.whitelist()
def get_delivery_order_generation_status(sales_order: str):
	"""Return whether the Sales Order currently has demand to generate."""
	if not frappe.db.exists("DocType", "Delivery Order"):
		from vuna_transport.services.demand.sales_transport_demand_service import get_generation_status

		return get_generation_status(sales_order)
	doc = _get_sales_order(sales_order)
	require_document_permission(doc, "read")

	if doc.docstatus != 1:
		return {"can_generate": False, "reason": _("The Sales Order must be submitted first.")}
	from vuna_transport.services.demand.source_policy_service import get_source_policy

	policy = get_source_policy("Sales Order")
	if not policy.enabled or (
		policy.required_workflow_state and doc.get("workflow_state") != policy.required_workflow_state
	):
		return {"can_generate": False, "reason": _("The source is disabled or awaiting transport approval.")}
	if not _requires_transport_planning(doc):
		return {
			"can_generate": False,
			"reason": _("This Delivery Method does not require transport planning."),
		}

	for item in doc.items:
		if (
			_is_stock_item(item)
			and _get_transport_quantity(item) > 0
			and not _is_item_already_linked(item.name)
		):
			return {"can_generate": True}

	return {"can_generate": False, "reason": _("All eligible stock items already have Delivery Orders.")}


@frappe.whitelist()
def generate_delivery_orders_for_sales_order(sales_order: str):
	"""Saved actions follow the site's explicit customer cutover mode."""
	from vuna_transport.services.demand.sales_transport_demand_service import (
		create_sales_demands,
		native_customer_demand_enabled,
	)

	if native_customer_demand_enabled():
		return create_sales_demands(sales_order)
	doc = _get_sales_order(sales_order)
	require_document_permission(doc, "read")
	require_doctype_permission("Delivery Order", "create")
	return create_delivery_orders_for_sales_order(doc)


def sync_delivery_order_quantities(delivery_order, persist=False):
	"""Refresh generated quantities from Sales Order Items without breaking allocations."""
	if not getattr(delivery_order, "sales_order", None):
		return False

	sales_order = _get_sales_order(delivery_order.sales_order)
	sales_items = {row.name: row for row in sales_order.items}
	changed = False
	for row in delivery_order.items:
		sales_item = sales_items.get(row.sales_order_item)
		if not sales_item:
			continue

		ordered_qty, transport_qty = get_sales_order_item_quantities(sales_item)
		reserved, allocated = _get_delivery_order_item_commitments(row.name)
		if transport_qty + 1e-9 < reserved + allocated:
			frappe.throw(
				_(
					"Sales Order quantity for item {0} cannot be reduced to {1}; "
					"{2} is already reserved/allocated on Transport Loads."
				).format(row.item_code, transport_qty, reserved + allocated)
			)

		measurements = _get_item_transport_measurements(sales_item, transport_qty)
		values = {
			"ordered_qty": ordered_qty,
			"transport_qty": transport_qty,
			"uom": getattr(sales_item, "uom", None),
			"conversion_factor": flt(getattr(sales_item, "conversion_factor", 1) or 1),
			"stock_qty": _get_stock_quantity(sales_item, transport_qty),
			"weight": measurements.weight,
			"weight_uom": measurements.weight_uom,
			"volume": measurements.volume,
			"volume_uom": measurements.volume_uom,
			"pallets": _get_item_measure(sales_item, "pallets", transport_qty),
			"cases": _get_item_measure(sales_item, "cases", transport_qty),
			"crates": _get_item_measure(sales_item, "crates", transport_qty),
		}
		for field, value in values.items():
			current = getattr(row, field, None)
			if current != value:
				setattr(row, field, value)
				changed = True

	if changed and persist:
		delivery_order.save(ignore_permissions=True)
		from vuna_transport.services.planning.load_validation_service import (
			sync_delivery_order_allocations,
		)

		sync_delivery_order_allocations([delivery_order.name])
	return changed


def sync_delivery_orders_after_direct_delivery_note(doc, method=None):
	"""Refresh Vuna demand after a Delivery Note created directly from a Sales Order."""
	if getattr(doc, "is_return", False):
		return
	if getattr(doc, "vuna_delivery_load", None):
		return
	sales_orders = {row.against_sales_order for row in doc.items if getattr(row, "against_sales_order", None)}
	for sales_order in sales_orders:
		delivery_orders = (
			frappe.get_all("Delivery Order", filters={"sales_order": sales_order}, pluck="name")
			if frappe.db.exists("DocType", "Delivery Order")
			else []
		)
		for delivery_order_name in delivery_orders:
			sync_delivery_order_quantities(
				frappe.get_doc("Delivery Order", delivery_order_name), persist=True
			)
		from vuna_transport.services.demand.sales_transport_demand_service import refresh_sales_demands

		refresh_sales_demands(frappe.get_doc("Sales Order", sales_order))


def get_sales_order_item_quantities(sales_order_item):
	"""Return authoritative ordered and outstanding transport quantities."""
	ordered_qty = flt(getattr(sales_order_item, "qty", 0))
	delivered_qty = flt(getattr(sales_order_item, "delivered_qty", 0))
	return ordered_qty, max(ordered_qty - delivered_qty, 0)


def get_open_sales_order_names(company=None):
	"""Return submitted Sales Orders that are still operationally open for transport."""
	filters = {"docstatus": 1, "status": ["not in", ["Closed", "Cancelled"]]}
	if company:
		filters["company"] = company
	return frappe.get_all("Sales Order", filters=filters, pluck="name")


def _get_delivery_order_item_commitments(delivery_order_item):
	row = frappe.db.sql(
		"""
		select
			coalesce(sum(case when dl.docstatus = 0 then dlo.allocated_quantity else 0 end), 0) as reserved,
			coalesce(sum(case when dl.docstatus = 1 then dlo.allocated_quantity else 0 end), 0) as allocated
		from `tabTransport Load Item` dlo
		join `tabTransport Load` dl on dl.name = dlo.parent
		where dlo.delivery_order_item = %s
		and dlo.parenttype = 'Transport Load'
		and dl.docstatus in (0, 1)
		""",
		delivery_order_item,
		as_dict=True,
	)[0]
	return flt(row.reserved), flt(row.allocated)


def _get_sales_order(sales_order):
	if isinstance(sales_order, str):
		return frappe.get_doc("Sales Order", sales_order)
	return sales_order


def _validate_sales_order(sales_order, check_generation_policy=True):
	if sales_order.doctype != "Sales Order":
		frappe.throw(_("Delivery Orders can only be generated from a Sales Order."))

	if sales_order.docstatus != 1:
		frappe.throw(_("The Sales Order must be submitted before Delivery Orders are generated."))
	if check_generation_policy and getattr(sales_order, "status", None) in ("Closed", "Cancelled"):
		frappe.throw(
			_("Sales Order {0} is {1} and cannot generate new Delivery Orders.").format(
				sales_order.name, sales_order.status
			)
		)

	settings = frappe.get_single("Transport Settings")
	if not settings.enabled:
		frappe.throw(_("Transport Management is disabled in Transport Settings."))
	from vuna_transport.services.demand.source_policy_service import require_source_enabled

	if check_generation_policy:
		require_source_enabled(sales_order)


def _get_transport_quantity(item):
	return get_sales_order_item_quantities(item)[1]


def _is_stock_item(sales_order_item):
	"""Return whether a Sales Order item represents transportable stock.

	``is_stock_item`` is authoritative on the ERPNext Item master.  The default
	keeps this helper tolerant of lightweight test doubles and older integrations;
	actual ERPNext Item documents always provide the field.
	"""
	item_code = getattr(sales_order_item, "item_code", None)
	if not item_code:
		return False

	item = frappe.get_cached_doc("Item", item_code)
	return bool(getattr(item, "is_stock_item", 1))


def _get_group_key(sales_order, item):
	delivery_address = (
		getattr(item, "customer_address", None)
		or getattr(sales_order, "customer_address", None)
		or getattr(sales_order, "shipping_address_name", None)
	)
	address_data = get_address_planning_data(delivery_address)
	values = {
		"delivery_date": getattr(item, "delivery_date", None) or getattr(sales_order, "delivery_date", None),
		"delivery_address": delivery_address,
		"source_warehouse": getattr(item, "warehouse", None),
		"transport_zone": getattr(item, "transport_zone", None) or address_data.delivery_zone,
		"preferred_route": getattr(item, "preferred_route", None),
		"handling_profile": getattr(item, "transport_handling_profile", None),
		"delivery_method": canonical_delivery_method(getattr(sales_order, "vuna_delivery_method", None)),
	}

	delivery_group = _get_item_delivery_group(item, getattr(sales_order, "company", None))
	return (*(_normalise_group_value(values[field]) for field in GROUPING_FIELDS), delivery_group)


def _normalise_group_value(value):
	if value is None:
		return None
	if hasattr(value, "isoformat"):
		return getdate(value).isoformat()
	return value


def _get_item_delivery_group(sales_order_item, company=None):
	if not getattr(sales_order_item, "item_code", None):
		return None
	item = frappe.get_cached_doc("Item", sales_order_item.item_code)
	group_name = getattr(item, "vuna_delivery_group", None)
	if not group_name:
		return None
	group = frappe.get_cached_doc("Delivery Group", group_name)
	if group.disabled:
		frappe.throw(
			_("Delivery Group {0} is disabled and cannot be used for new customer demand.").format(group.name)
		)
	if group.company and company and group.company != company:
		frappe.throw(
			_("Delivery Group {0} belongs to company {1}, not {2}.").format(
				group.name, group.company, company
			)
		)
	return group.name


def _is_item_already_linked(sales_order_item):
	legacy = bool(
		frappe.get_all(
			"Delivery Order Item",
			filters={"sales_order_item": sales_order_item},
			limit=1,
		)
	)
	return legacy or bool(
		frappe.db.sql_list(
			"""select i.name from `tabTransport Demand Item` i
		join `tabTransport Demand` d on d.name=i.parent
		where i.source_item=%s and d.source_document_type='Sales Order'
		and d.docstatus!=2 and i.parenttype='Transport Demand' limit 1""",
			sales_order_item,
		)
	)


def _create_delivery_order(sales_order, group_key, group_items):
	_validate_group_key(group_key[0], group_key[2])
	delivery_order = frappe.new_doc("Delivery Order")
	delivery_order.update(build_sales_planning_snapshot(sales_order, group_key, group_items))
	delivery_order.insert(ignore_permissions=True)
	if _should_auto_submit_delivery_orders():
		delivery_order.flags.ignore_permissions = True
		delivery_order.submit()
	return delivery_order.name


def build_sales_planning_snapshot(sales_order, group_key, group_items):
	(
		delivery_date,
		delivery_address,
		source_warehouse,
		transport_zone,
		preferred_route,
		handling_profile,
		delivery_method,
		delivery_group,
	) = group_key
	if not delivery_date:
		frappe.throw(_("A required delivery date is needed for customer transport demand."))
	delivery_order = frappe._dict()
	rows = []

	delivery_order.company = sales_order.company
	delivery_order.source_document_type = "Sales Order"
	delivery_order.source_document = sales_order.name
	delivery_order.sales_order = sales_order.name
	delivery_order.customer = sales_order.customer
	delivery_order.customer_name = sales_order.customer_name
	delivery_order.delivery_address = delivery_address
	address_data = get_address_planning_data(delivery_address)
	delivery_order.territory = getattr(address_data, "territory", None)
	delivery_order.county = getattr(address_data, "county", None)
	delivery_order.constituency = getattr(address_data, "constituency", None)
	delivery_order.town = getattr(address_data, "town", None)
	delivery_order.required_delivery_date = delivery_date
	delivery_order.source_warehouse = source_warehouse
	delivery_order.transport_zone = transport_zone
	delivery_order.preferred_route = preferred_route
	delivery_order.transport_handling_profile = handling_profile
	delivery_order.delivery_method = delivery_method
	delivery_order.delivery_group = delivery_group
	delivery_order.status = "Open"
	requirements = _get_group_transport_requirements(group_items, handling_profile, sales_order.company)
	delivery_order.transport_compatibility_group = requirements.transport_compatibility_group
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
		row_values = _get_delivery_order_item_values(
			item,
			transport_qty,
			requirements.transport_compatibility_group,
			delivery_group,
		)
		rows.append(row_values)
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

	delivery_order["items"] = rows
	return delivery_order


def _should_auto_submit_delivery_orders():
	from vuna_transport.services.demand.source_policy_service import get_source_policy

	return bool(get_source_policy("Sales Order").auto_submit_delivery_orders)


def _get_group_transport_requirements(group_items, handling_profile=None, company=None):
	items = [frappe.get_cached_doc("Item", row.item_code) for row, _quantity in group_items]
	body_types = {
		item.vuna_required_body_type for item in items if getattr(item, "vuna_required_body_type", None)
	}
	if len(body_types) > 1:
		frappe.throw(
			_("Items in one customer demand require conflicting vehicle body types: {0}.").format(
				", ".join(sorted(body_types))
			)
		)
	equipment = sorted(
		{
			value.strip()
			for item in items
			for value in (getattr(item, "vuna_special_equipment_required", None) or "")
			.replace("\n", ",")
			.split(",")
			if value.strip()
		}
	)
	profile = (handling_profile or "").lower()
	compatibility_groups = {
		_get_transport_compatibility_group(item, company)
		for item in items
		if getattr(item, "vuna_transport_compatibility_group", None)
	}
	return frappe._dict(
		refrigeration_required=any(getattr(item, "vuna_refrigeration_required", 0) for item in items)
		or any(keyword in profile for keyword in ("refriger", "cold chain", "chilled", "frozen")),
		tail_lift_required=any(getattr(item, "vuna_tail_lift_required", 0) for item in items)
		or "tail lift" in profile,
		required_body_type=next(iter(body_types), None),
		special_equipment_required=", ".join(equipment),
		transport_compatibility_group=(
			next(iter(compatibility_groups)) if len(compatibility_groups) == 1 else None
		),
	)


def _get_transport_compatibility_group(item, company=None):
	group_name = (getattr(item, "vuna_transport_compatibility_group", None) or "").strip()
	if not group_name:
		return None
	group = frappe.get_cached_doc("Transport Compatibility Group", group_name)
	if group.disabled:
		frappe.throw(
			_(
				"Transport Compatibility Group {0} is disabled and cannot be used for new customer demand."
			).format(group.name)
		)
	if group.company and company and group.company != company:
		frappe.throw(
			_("Transport Compatibility Group {0} belongs to company {1}, not {2}.").format(
				group.name, group.company, company
			)
		)
	return group.name


def _requires_transport_planning(sales_order):
	delivery_method = getattr(sales_order, "vuna_delivery_method", None)
	if not delivery_method:
		return True
	workflow = validate_sales_order_delivery_method(sales_order)
	return bool(workflow.requires_transport_planning or workflow.transport_arranged_by == "Company")


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


def _log_non_stock_item(sales_order, item):
	frappe.logger("vuna_transport").info(
		"Skipped Delivery Order generation for Sales Order %s item %s because item %s is non-stock/service.",
		sales_order.name,
		item.name,
		getattr(item, "item_code", None),
	)


def _get_delivery_order_item_values(
	item, transport_qty, transport_compatibility_group=None, delivery_group=None
):
	measurements = _get_item_transport_measurements(item, transport_qty)
	values = {
		"sales_order_item": item.name,
		"item_code": item.item_code,
		"item_name": getattr(item, "item_name", None),
		"ordered_qty": flt(getattr(item, "qty", 0)),
		"transport_qty": transport_qty,
		"uom": getattr(item, "uom", None),
		"conversion_factor": flt(getattr(item, "conversion_factor", 1) or 1),
		"stock_qty": _get_stock_quantity(item, transport_qty),
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
		"transport_compatibility_group": transport_compatibility_group
		or getattr(item, "vuna_transport_compatibility_group", None),
		"delivery_group": delivery_group,
	}
	return values


def _get_item_transport_measurements(sales_order_item, transport_qty):
	item = frappe.get_cached_doc("Item", sales_order_item.item_code)
	uoms = get_transport_uoms()
	stock_quantity = _get_stock_quantity(sales_order_item, transport_qty)
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


def _get_stock_quantity(sales_order_item, quantity):
	return flt(quantity) * flt(getattr(sales_order_item, "conversion_factor", 1) or 1)


def _get_item_measure(item, measure, quantity):
	value = getattr(item, measure, None)
	if value is not None:
		return flt(value)

	per_unit = getattr(item, f"{measure}_per_unit", None)
	return flt(per_unit) * quantity if per_unit is not None else 0
