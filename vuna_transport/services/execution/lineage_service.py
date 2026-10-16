"""Maintain ERPNext document lineage back to Vuna transport planning records."""

import frappe
from frappe import _
from frappe.utils import flt

from vuna_transport.services.execution.dispatch_guard_service import (
	get_locked_delivery_load,
	set_delivery_load_status,
	validate_load_is_operational,
)
from vuna_transport.services.fleet.vehicle_allocation_service import (
	get_active_allocation,
	get_allocation_load_names,
)


def populate_delivery_note_delivery_links(doc, method=None):
	if not doc.meta.has_field("vuna_delivery_load"):
		return
	if not doc.vuna_delivery_load:
		pick_lists = {row.against_pick_list for row in doc.items if row.against_pick_list}
		if not pick_lists:
			pick_list_items = [row.pick_list_item for row in doc.items if row.pick_list_item]
			pick_lists = (
				set(
					frappe.get_all(
						"Pick List Item",
						filters={"name": ["in", pick_list_items]},
						pluck="parent",
					)
				)
				if pick_list_items
				else set()
			)

		loads = {
			frappe.db.get_value("Pick List", pick_list, "vuna_delivery_load") for pick_list in pick_lists
		}
		loads.discard(None)
		if len(loads) == 1:
			doc.vuna_delivery_load = loads.pop()

	if not doc.vuna_delivery_load:
		return
	load = get_locked_delivery_load(doc.vuna_delivery_load)
	validate_load_is_operational(load, _("creating or updating a Delivery Note"))
	if not doc.vuna_delivery_order:
		delivery_orders = frappe.get_all(
			"Delivery Load Order",
			filters={
				"parent": load.name,
				"customer": doc.customer,
				"delivery_address": doc.shipping_address_name,
			},
			pluck="delivery_order",
		)
		if len(set(delivery_orders)) == 1:
			doc.vuna_delivery_order = delivery_orders[0]
	_validate_delivery_note_against_load(doc, load)


def _validate_delivery_note_against_load(doc, load):
	if not doc.vuna_delivery_order:
		frappe.throw(
			_("Delivery Note must identify its Delivery Order for Delivery Load {0}.").format(load.name)
		)
	load_rows = frappe.get_all(
		"Delivery Load Order",
		filters={"parent": load.name, "delivery_order": doc.vuna_delivery_order},
		fields=["delivery_order_item", "allocated_quantity"],
	)
	if not load_rows:
		frappe.throw(
			_("Delivery Order {0} does not belong to Delivery Load {1}.").format(
				doc.vuna_delivery_order, load.name
			)
		)
	if doc.company != load.company:
		frappe.throw(_("Delivery Note and Delivery Load must belong to the same company."))
	warehouse_companies = {
		frappe.db.get_value("Warehouse", item.warehouse, "company") for item in doc.items if item.warehouse
	}
	warehouse_companies.discard(None)
	if warehouse_companies and warehouse_companies != {load.company}:
		frappe.throw(_("Every Delivery Note item warehouse must belong to the Delivery Load company."))

	item_allocations = {}
	for row in load_rows:
		if not row.delivery_order_item:
			continue
		if row.delivery_order_item in item_allocations:
			frappe.throw(
				_("Delivery Order Item {0} appears more than once on Delivery Load {1}.").format(
					row.delivery_order_item, load.name
				)
			)
		item_allocations[row.delivery_order_item] = flt(row.allocated_quantity)
	order_items = frappe.get_all(
		"Delivery Order Item",
		filters={"name": ["in", list(item_allocations)]},
		fields=["name", "sales_order_item"],
	)
	allocated_by_sales_order_item = {
		row.sales_order_item: flt(item_allocations[row.name]) for row in order_items
	}
	validate_delivery_note_item_allocations(
		doc.items, allocated_by_sales_order_item, load.name, doc.vuna_delivery_order, doc.name
	)


def validate_delivery_note_item_allocations(
	items, allocated_by_sales_order_item, load_name, delivery_order=None, current_name=None
):
	"""Validate cumulative Delivery Note quantities against one load allocation.

	One Delivery Order may be fulfilled by multiple Delivery Notes. The sum of
	active notes, excluding the note currently being validated, must stay within
	the load's allocation.
	"""
	existing_quantities = {}
	if delivery_order and allocated_by_sales_order_item:
		existing_rows = frappe.db.sql(
			"""
				select dni.so_detail, coalesce(sum(dni.qty), 0) as quantity
				from `tabDelivery Note Item` dni
				join `tabDelivery Note` dn on dn.name = dni.parent
				where dn.vuna_delivery_load = %(load_name)s
				and dn.vuna_delivery_order = %(delivery_order)s
				and dn.docstatus != 2
				and dn.name != %(current_name)s
				group by dni.so_detail
			""",
			{
				"load_name": load_name,
				"delivery_order": delivery_order,
				"current_name": current_name or "",
			},
			as_dict=True,
		)
		existing_quantities = {
			(row.get("so_detail") if isinstance(row, dict) else row.so_detail): flt(
				row.get("quantity") if isinstance(row, dict) else row.quantity
			)
			for row in existing_rows
		}

	current_quantities = {}
	for item in items:
		if item.so_detail not in allocated_by_sales_order_item:
			frappe.throw(
				_("Delivery Note item {0} is not allocated on Delivery Load {1}.").format(
					item.item_code, load_name
				)
			)
		current_quantities[item.so_detail] = current_quantities.get(item.so_detail, 0) + flt(
			getattr(item, "qty", None)
		)
	for so_detail, quantity in current_quantities.items():
		if existing_quantities.get(so_detail, 0) + quantity > flt(allocated_by_sales_order_item[so_detail]):
			frappe.throw(
				_(
					"Cumulative Delivery Note quantity for Sales Order Item {0} exceeds its load allocation."
				).format(so_detail)
			)


def validate_delivery_trip(doc, method=None):
	loads = get_delivery_trip_loads(doc, lock=True)
	if not loads:
		return
	load = loads[0]
	for selected_load in loads:
		validate_load_is_operational(selected_load, _("creating or updating a Delivery Trip"))
	existing_trip = frappe.db.get_value(
		"Delivery Stop",
		{
			"vuna_delivery_load": ["in", [selected_load.name for selected_load in loads]],
			"parent": ["!=", doc.name or ""],
			"docstatus": ["!=", 2],
		},
		"parent",
	)
	if existing_trip:
		frappe.throw(
			_("Delivery Load {0} already has active Delivery Trip {1}.").format(load.name, existing_trip)
		)
	if doc.company != load.company or any(selected_load.company != load.company for selected_load in loads):
		frappe.throw(_("Delivery Trip and Delivery Load must belong to the same company."))
	requires_allocation = True
	if load.delivery_method:
		requires_allocation = bool(
			frappe.db.get_value("Delivery Method", load.delivery_method, "requires_vehicle_allocation")
		)
	allocation = get_active_allocation(load, required=requires_allocation)
	if allocation and set(get_allocation_load_names(allocation)) != {
		selected_load.name for selected_load in loads
	}:
		frappe.throw(_("Delivery Trip loads must match the active Vehicle Allocation."))
	if allocation:
		if not allocation.vehicle:
			frappe.throw(_("The active Vehicle Allocation must have an ERPNext Vehicle."))
		if not allocation.driver:
			frappe.throw(_("The active Vehicle Allocation must have an ERPNext Driver."))
		if doc.docstatus == 0 or doc.status in ("Draft", "Scheduled"):
			if doc.vuna_vehicle_allocation != allocation.name:
				frappe.throw(_("Delivery Trip must use the active Vehicle Allocation for this load."))
			if allocation.vehicle and doc.vehicle != allocation.vehicle:
				frappe.throw(
					_("Delivery Trip vehicle must match Vehicle Allocation {0}.").format(allocation.name)
				)

			if allocation.driver and doc.driver != allocation.driver:
				frappe.throw(
					_("Delivery Trip driver must match Vehicle Allocation {0}.").format(allocation.name)
				)
		else:
			if doc.vuna_current_vehicle_allocation != allocation.name:
				frappe.throw(_("Current Vehicle Allocation must match the load's active allocation."))
			if doc.vuna_current_vehicle != allocation.vehicle:
				frappe.throw(
					_("Current Execution Vehicle must match Vehicle Allocation {0}.").format(allocation.name)
				)
			if doc.vuna_current_driver != allocation.driver:
				frappe.throw(
					_("Current Execution Driver must match Vehicle Allocation {0}.").format(allocation.name)
				)

	execution_vehicle = doc.vuna_current_vehicle or doc.vehicle
	if execution_vehicle and load.required_vehicle_category:
		vehicle_category = frappe.db.get_value("Vehicle", execution_vehicle, "vuna_vehicle_category")
		from vuna_transport.services.fleet.vehicle_allocation_service import (
			_validate_vehicle_category_for_loads,
		)

		_validate_vehicle_category_for_loads(loads, vehicle_category)

	stop_delivery_notes = [stop.delivery_note for stop in doc.delivery_stops if stop.delivery_note]
	expected_delivery_notes = set(
		get_delivery_notes_for_loads([selected_load.name for selected_load in loads], docstatus=1)
	)
	validate_delivery_trip_note_set(expected_delivery_notes, stop_delivery_notes)
	validate_delivery_note_quantities_for_loads(loads)

	for stop in doc.delivery_stops:
		if not stop.delivery_note:
			continue
		delivery_note = frappe.db.get_value(
			"Delivery Note",
			stop.delivery_note,
			["vuna_delivery_load", "vuna_delivery_order"],
			as_dict=True,
		)
		if not delivery_note or (
			delivery_note.vuna_delivery_load not in {selected_load.name for selected_load in loads}
			and not frappe.db.exists(
				"Delivery Note Item",
				{"parent": stop.delivery_note, "vuna_delivery_load_order": ["is", "set"]},
			)
		):
			frappe.throw(
				_("Delivery Note {0} does not belong to this Vehicle Allocation's Delivery Loads.").format(
					stop.delivery_note
				)
			)
		stop.vuna_delivery_order = delivery_note.vuna_delivery_order
		stop.vuna_delivery_load = delivery_note.vuna_delivery_load
		if stop.vuna_delivery_load not in {selected_load.name for selected_load in loads}:
			load_order = frappe.db.get_value(
				"Delivery Note Item",
				{"parent": stop.delivery_note, "vuna_delivery_load_order": ["is", "set"]},
				"vuna_delivery_load_order",
			)
			if load_order:
				stop.vuna_delivery_load = frappe.db.get_value("Delivery Load Order", load_order, "parent")
				stop.vuna_delivery_order = frappe.db.get_value(
					"Delivery Load Order", load_order, "delivery_order"
				)
		load_rows = frappe.get_all(
			"Delivery Load Order",
			filters={
				"parent": stop.vuna_delivery_load,
				"delivery_order": stop.vuna_delivery_order,
			},
			pluck="name",
		)
		stop.vuna_delivery_load_order = load_rows[0] if len(load_rows) == 1 else None


def get_delivery_trip_load_names(doc):
	"""Return every Delivery Load executed by a Delivery Trip."""
	stop_names = [
		row.vuna_delivery_load
		for row in (getattr(doc, "delivery_stops", None) or [])
		if getattr(row, "vuna_delivery_load", None)
	]
	stop_names.extend(
		frappe.get_all(
			"Delivery Stop",
			filters={"parent": doc.name, "parenttype": "Delivery Trip", "vuna_delivery_load": ["is", "set"]},
			pluck="vuna_delivery_load",
		)
		if getattr(doc, "name", None)
		else []
	)

	allocation_name = getattr(doc, "vuna_vehicle_allocation", None) or getattr(
		doc, "vuna_current_vehicle_allocation", None
	)
	if allocation_name and frappe.db.exists("Vehicle Allocation", allocation_name):
		allocation = frappe.get_doc("Vehicle Allocation", allocation_name)
		names = get_allocation_load_names(allocation)
		if names:
			stop_names.extend(names)
	return list(dict.fromkeys(stop_names))


def get_delivery_notes_for_loads(load_names, *, docstatus=None):
	"""Return Delivery Notes linked through parent or item-level load lineage."""
	if not load_names:
		return []
	status_condition = ""
	values = {"load_names": tuple(load_names)}
	if docstatus is not None:
		status_condition = "and dn.docstatus = %(docstatus)s"
		values["docstatus"] = docstatus
	return frappe.db.sql(
		f"""
		select distinct dn.name
		from `tabDelivery Note` dn
		left join `tabDelivery Note Item` dni on dni.parent = dn.name
		left join `tabDelivery Load Order` dlo on dlo.name = dni.vuna_delivery_load_order
		where (dn.vuna_delivery_load in %(load_names)s or dlo.parent in %(load_names)s)
		{status_condition}
		and dn.docstatus != 2
		order by dn.creation asc
		""",
		values,
		pluck="name",
	)


def get_delivery_trip_loads(doc, *, lock=False):
	return [
		get_locked_delivery_load(name) if lock else frappe.get_doc("Delivery Load", name)
		for name in get_delivery_trip_load_names(doc)
	]


def validate_delivery_trip_note_set(expected_delivery_notes, stop_delivery_notes):
	expected_delivery_notes = set(expected_delivery_notes)
	stop_delivery_note_set = set(stop_delivery_notes)
	if len(stop_delivery_notes) != len(set(stop_delivery_notes)):
		frappe.throw(_("A Delivery Note cannot appear more than once on the same Delivery Trip."))
	if not expected_delivery_notes:
		frappe.throw(_("Create and submit the Delivery Notes for this load first."))
	if stop_delivery_note_set != expected_delivery_notes:
		missing = sorted(expected_delivery_notes - stop_delivery_note_set)
		extra = sorted(stop_delivery_note_set - expected_delivery_notes)
		details = []
		if missing:
			details.append(_("missing: {0}").format(", ".join(missing)))
		if extra:
			details.append(_("not part of this load: {0}").format(", ".join(extra)))
		frappe.throw(
			_("Delivery Trip stops must exactly match the load's submitted Delivery Notes ({0}).").format(
				"; ".join(details) or _("no submitted Delivery Notes")
			)
		)


def validate_delivery_note_quantities_for_loads(loads):
	"""Ensure submitted Delivery Notes cover every allocated item quantity."""
	load_names = [load.name for load in loads if getattr(load, "name", None)]
	if not load_names:
		return
	load_rows = frappe.get_all(
		"Delivery Load Order",
		filters={"parent": ["in", load_names], "parenttype": "Delivery Load"},
		fields=["parent", "delivery_order_item", "allocated_quantity"],
	)
	if not load_rows:
		return

	item_names = list({row.delivery_order_item for row in load_rows if row.delivery_order_item})
	item_sales_orders = {
		row.name: row.sales_order_item
		for row in frappe.get_all(
			"Delivery Order Item",
			filters={"name": ["in", item_names]},
			fields=["name", "sales_order_item"],
		)
	}
	actual_rows = frappe.db.sql(
		"""
			select coalesce(dlo.parent, dn.vuna_delivery_load) as load_name,
				dni.so_detail, coalesce(sum(dni.qty), 0) as quantity
			from `tabDelivery Note` dn
			join `tabDelivery Note Item` dni on dni.parent = dn.name
			left join `tabDelivery Load Order` dlo on dlo.name = dni.vuna_delivery_load_order
			where (dn.vuna_delivery_load in %(load_names)s or dlo.parent in %(load_names)s)
			and dn.docstatus = 1
			group by coalesce(dlo.parent, dn.vuna_delivery_load), dni.so_detail
		""",
		{"load_names": tuple(load_names)},
		as_dict=True,
	)
	actual = {
		(_row_value(row, "load_name"), _row_value(row, "so_detail")): flt(_row_value(row, "quantity"))
		for row in actual_rows
	}

	for row in load_rows:
		sales_order_item = item_sales_orders.get(row.delivery_order_item)
		allocated = flt(row.allocated_quantity)
		delivered = actual.get((row.parent, sales_order_item), 0)
		if delivered > allocated:
			frappe.throw(
				_(
					"Submitted Delivery Note quantity for item {0} exceeds Delivery Load {1} allocation."
				).format(row.delivery_order_item, row.parent)
			)
		if delivered < allocated:
			frappe.throw(
				_(
					"Submitted Delivery Notes are missing {0} of item {1} allocated on Delivery Load {2}."
				).format(allocated - delivered, row.delivery_order_item, row.parent)
			)


def _row_value(row, fieldname, default=None):
	"""Read a field from either a Frappe row object or a test/database dict."""
	if isinstance(row, dict):
		return row.get(fieldname, default)
	return getattr(row, fieldname, default)


def link_delivery_trip(doc, method=None):
	if not get_delivery_trip_load_names(doc):
		return
	for load_name in get_delivery_trip_load_names(doc):
		linked_trip = frappe.db.get_value("Delivery Load", load_name, "delivery_trip")
		if linked_trip and linked_trip != doc.name:
			frappe.throw(
				_("Delivery Load {0} is already linked to Delivery Trip {1}.").format(load_name, linked_trip)
			)
		if linked_trip != doc.name:
			frappe.db.set_value("Delivery Load", load_name, "delivery_trip", doc.name, update_modified=False)


def submit_delivery_trip(doc, method=None):
	if get_delivery_trip_load_names(doc):
		for load_name in get_delivery_trip_load_names(doc):
			set_delivery_load_status(
				load_name, "Dispatched", _("Delivery Trip {0} submitted").format(doc.name)
			)
			_recompute_delivery_order_statuses(load_name)


def cancel_delivery_trip(doc, method=None):
	if get_delivery_trip_load_names(doc):
		for load_name in get_delivery_trip_load_names(doc):
			set_delivery_load_status(
				load_name, "Ready for Dispatch", _("Delivery Trip {0} cancelled").format(doc.name)
			)
			_recompute_delivery_order_statuses(load_name)


def prepare_delivery_trip_cancellation(doc, method=None):
	"""Keep submitted POD history in order while releasing planning links."""
	submitted_pods = frappe.get_all(
		"Proof of Delivery",
		filters={"delivery_trip": doc.name, "docstatus": 1},
		pluck="name",
	)
	if submitted_pods:
		frappe.throw(
			_("Cancel the submitted Proofs of Delivery before cancelling Delivery Trip {0}: {1}.").format(
				doc.name, ", ".join(submitted_pods)
			)
		)

	# Delivery Trip cancellation unlinks Delivery Load in the on_cancel flow.
	# Vehicle Allocation remains historical and must not make trip cancellation
	# impossible merely because it references this execution trip.
	doc.ignore_linked_doctypes = ["Delivery Load", "Vehicle Allocation"]


def unlink_delivery_trip(doc, method=None):
	if not get_delivery_trip_load_names(doc):
		return
	for load_name in get_delivery_trip_load_names(doc):
		if frappe.db.get_value("Delivery Load", load_name, "delivery_trip") == doc.name:
			frappe.db.set_value("Delivery Load", load_name, "delivery_trip", None, update_modified=False)


def complete_load_without_trip(doc, method=None):
	load_name = getattr(doc, "vuna_delivery_load", None)
	if not load_name or doc.docstatus != 1:
		return
	load = frappe.get_doc("Delivery Load", load_name)
	if not load.delivery_method:
		return
	if frappe.db.get_value("Delivery Method", load.delivery_method, "requires_delivery_trip"):
		return
	expected = {row.delivery_order for row in load.delivery_orders if row.delivery_order}
	submitted = set(
		frappe.get_all(
			"Delivery Note",
			filters={"vuna_delivery_load": load.name, "docstatus": 1},
			pluck="vuna_delivery_order",
		)
	)
	if expected and expected.issubset(submitted):
		set_delivery_load_status(load, "Completed", _("All required Delivery Notes were submitted"))
		_recompute_delivery_order_statuses(load.name)


def reconcile_load_after_delivery_note_cancel(doc, method=None):
	load_name = getattr(doc, "vuna_delivery_load", None)
	if not load_name:
		return
	load = get_locked_delivery_load(load_name)
	if load.delivery_trip and frappe.db.get_value("Delivery Trip", load.delivery_trip, "docstatus") != 2:
		return
	if load.status in ("Completed", "Completed with Exceptions"):
		set_delivery_load_status(
			load,
			"Ready for Dispatch",
			_("Delivery Note {0} cancelled").format(doc.name),
		)
	_recompute_delivery_order_statuses(load.name)


def _recompute_delivery_order_statuses(delivery_load):
	from vuna_transport.services.execution.pod_service import recompute_delivery_order_status

	for delivery_order in _get_delivery_orders(delivery_load):
		recompute_delivery_order_status(delivery_order)


def _get_delivery_orders(delivery_load):
	return set(
		frappe.get_all(
			"Delivery Load Order",
			filters={"parent": delivery_load, "parenttype": "Delivery Load"},
			pluck="delivery_order",
		)
	)
