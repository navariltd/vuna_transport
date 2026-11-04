"""Task-based transfer/mixed trip planning; no proof or stock posting here."""

import hashlib
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from vuna_transport.services.planning.transport_load_service import is_demand_load
from vuna_transport.services.routing.location_service import get_warehouse_planning_data
from vuna_transport.services.shared.security_service import require_document_permission

TERMINAL_TASK_STATUSES = ("Completed", "Completed with Variance", "Failed")


def task_key(*parts):
	return hashlib.sha256(frappe.as_json(parts).encode()).hexdigest()


def is_task_trip(doc):
	return bool(getattr(doc, "vuna_task_based", 0))


def plan_tasks(loads):
	"""Build tasks from authoritative load rows, retaining standard DN/POD units."""
	from vuna_transport.services.execution.lineage_service import (
		get_delivery_notes_for_loads,
		validate_delivery_note_quantities_for_loads,
	)
	from vuna_transport.services.fleet.vehicle_allocation_service import get_delivery_note_submission_status

	specs = []
	customers = []
	for load in loads:
		require_document_permission(load, "read")
		if not is_demand_load(load):
			customers.append(load)
			continue
		grouped = defaultdict(list)
		for row in load.delivery_orders:
			grouped[row.transport_demand].append(row)
		for name, rows in grouped.items():
			demand = frappe.get_doc("Transport Demand", name)
			require_document_permission(demand, "read")
			if (
				demand.docstatus != 1
				or demand.movement_type != "Warehouse Transfer"
				or demand.company != load.company
				or (demand.source_warehouse and demand.source_warehouse != load.source_warehouse)
			):
				frappe.throw(_("Transfer demand does not match the selected load."))
			items = {row.name: row for row in demand.items}
			cargo = []
			for row in rows:
				item = items.get(row.transport_demand_item)
				if not item or row.item_code != item.item_code or flt(row.allocated_quantity) <= 0:
					frappe.throw(_("Transfer item lineage is invalid."))
				cargo.append(
					{
						"load_item": row.name,
						"item_code": row.item_code,
						"qty": row.allocated_quantity,
						"uom": item.transaction_uom,
						"conversion_factor": item.conversion_factor,
						"stock_qty": flt(row.get("allocated_stock_qty"))
						or flt(row.allocated_quantity) * flt(item.conversion_factor),
						"stock_uom": item.stock_uom,
					}
				)
			# Loading is completed on allocation before the trip. Only the actual
			# receiving destination is a driver stop/POD operation.
			for kind, warehouse in (("Transfer Receipt", demand.destination_warehouse),):
				address = get_warehouse_planning_data(warehouse).get("address")
				if not address:
					frappe.throw(
						_("Link an Address to Warehouse {0} before planning its trip.").format(warehouse)
					)
				specs.append(
					{
						"task_type": kind,
						"transport_load": load.name,
						"transport_demand": name,
						"address": address,
						"delivery_note": None,
						"items": cargo,
						"task_key": task_key(kind, load.name, name),
					}
				)
	if customers:
		for load in customers:
			if not get_delivery_note_submission_status(load)["ready"]:
				frappe.throw(_("Submit Delivery Notes for customer loads before creating this trip."))
		validate_delivery_note_quantities_for_loads(customers)
		rows = {row.name: (load, row) for load in customers for row in load.delivery_orders}
		for name in get_delivery_notes_for_loads([load.name for load in customers], docstatus=1):
			note = frappe.get_doc("Delivery Note", name)
			require_document_permission(note, "read")
			if note.is_return:
				frappe.throw(_("Return Delivery Notes cannot be outbound customer tasks."))
			address = note.shipping_address_name or note.customer_address
			if not address:
				frappe.throw(_("Delivery Note {0} requires a delivery Address.").format(name))
			cargo = [
				{
					"load_item": item.vuna_delivery_load_order,
					"item_code": item.item_code,
					"qty": item.qty,
					"uom": item.uom,
					"conversion_factor": item.conversion_factor,
					"stock_qty": item.stock_qty,
					"stock_uom": item.stock_uom,
				}
				for item in note.items
				if item.vuna_delivery_load_order in rows
			]
			if not cargo:
				frappe.throw(_("Delivery Note items are missing load-row lineage."))
			specs.append(
				{
					"task_type": "Customer Delivery",
					"transport_load": rows[cargo[0]["load_item"]][0].name,
					"transport_demand": None,
					"delivery_note": name,
					"customer": note.customer,
					"address": address,
					"items": cargo,
					"task_key": task_key("Customer Delivery", name),
				}
			)
	if len(specs) > 500 or sum(len(spec["items"]) for spec in specs) > 5000:
		frappe.throw(
			_(
				"Split this allocation into smaller trips: the planning limit is 500 tasks and 5,000 task item rows."
			)
		)
	return specs


def group_stops(specs):
	"""Group physical delivery destinations; loading is not a driver stop."""

	def single(spec):
		return {
			"address": spec["address"],
			"delivery_note": spec.get("delivery_note"),
			"customer": spec.get("customer"),
			"vuna_delivery_load": spec["transport_load"],
			"tasks": [spec],
		}

	stops = []
	for spec in sorted(specs, key=lambda value: value["task_type"] != "Customer Delivery"):
		target = next(
			(stop for stop in stops if stop["address"] == spec["address"] and not spec.get("delivery_note")),
			None,
		)
		if target is None:
			stops.append(single(spec))
		else:
			target["tasks"].append(spec)

	ordered = stops
	merged = []
	for stop in ordered:
		can_merge = bool(
			merged
			and merged[-1]["address"] == stop["address"]
			and not (merged[-1].get("delivery_note") and stop.get("delivery_note"))
		)
		if can_merge:
			legs = defaultdict(set)
			for task in merged[-1]["tasks"] + stop["tasks"]:
				if task["task_type"].startswith("Transfer"):
					legs[(task["transport_load"], task["transport_demand"])].add(task["task_type"])
			can_merge = not any(len(kinds) > 1 for kinds in legs.values())
		if can_merge:
			merged[-1]["tasks"].extend(stop["tasks"])
			if stop.get("delivery_note"):
				merged[-1].update({key: stop[key] for key in ("delivery_note", "customer")})
		else:
			merged.append(stop)
	for stop in merged:
		stop["vuna_task_keys"] = frappe.as_json([task["task_key"] for task in stop["tasks"]])
	return merged


def make_task_trip(allocation, loads):
	require_document_permission(allocation, "write")
	if allocation.docstatus != 1 or not allocation.vehicle or not allocation.driver:
		frappe.throw(_("Submit an allocation with a vehicle and driver before creating its trip."))
	existing = frappe.db.get_value(
		"Delivery Trip", {"vuna_vehicle_allocation": allocation.name, "docstatus": ["!=", 2]}, "name"
	)
	if existing:
		return frappe.get_doc("Delivery Trip", existing)
	from vuna_transport.services.execution.allocation_transfer_service import allocation_stock_entry

	for load in loads:
		if is_demand_load(load):
			stock = allocation_stock_entry(allocation.name, load.name)
			if not stock or frappe.db.get_value("Stock Entry", stock, "docstatus") != 1:
				frappe.throw(
					_(
						"Submit the allocation's Add-to-Transit Stock Entry for {0} before creating the trip."
					).format(load.name)
				)
	trip = frappe.new_doc("Delivery Trip")
	trip.company = allocation.company
	trip.vehicle = trip.vuna_current_vehicle = allocation.vehicle
	trip.driver = trip.vuna_current_driver = allocation.driver
	trip.vuna_vehicle_allocation = trip.vuna_current_vehicle_allocation = allocation.name
	trip.departure_time = allocation.planned_reporting_time or now_datetime()
	trip.vuna_task_based = 1
	for stop in group_stops(plan_tasks(loads)):
		trip.append("delivery_stops", {key: value for key, value in stop.items() if key != "tasks"})
	return trip


def map_tasks_to_stops(specs, stops):
	expected_notes = {spec["delivery_note"] for spec in specs if spec.get("delivery_note")}
	actual_notes = [stop.delivery_note for stop in stops if stop.delivery_note]
	if set(actual_notes) != expected_notes or len(actual_notes) != len(set(actual_notes)):
		frappe.throw(_("Trip Delivery Notes must exactly match the planned customer tasks."))
	assignments = []
	claimed = set()
	keys_by_stop = {}
	for stop in stops:
		keys = frappe.parse_json(stop.get("vuna_task_keys") or "[]")
		if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
			frappe.throw(_("Transport task assignments are invalid."))
		if claimed.intersection(keys) or len(keys) != len(set(keys)):
			frappe.throw(_("A transport task cannot belong to multiple stops."))
		claimed.update(keys)
		keys_by_stop[stop.name] = keys
	if claimed != {spec["task_key"] for spec in specs}:
		frappe.throw(_("Trip task assignments must match the selected loads."))
	for spec in specs:
		matches = [
			stop
			for stop in stops
			if spec["task_key"] in keys_by_stop[stop.name]
			and stop.address == spec["address"]
			and (not spec.get("delivery_note") or stop.delivery_note == spec["delivery_note"])
		]
		if not matches:
			frappe.throw(_("Trip stops must include all planned task addresses and Delivery Notes."))
		assignments.append((spec, matches[0]))
	used = {stop.name for _, stop in assignments}
	if used != {stop.name for stop in stops}:
		frappe.throw(_("Remove stops that have no assigned transport task."))
	positions = {}
	for spec, stop in assignments:
		if spec["task_type"].startswith("Transfer"):
			positions.setdefault((spec["transport_load"], spec["transport_demand"]), {})[
				spec["task_type"]
			] = stop.idx
	# Old submitted manifests may retain historical loading records. Their old
	# order is checked only when both sides exist; new plans have destinations only.
	if any(
		value["Transfer Dispatch"] >= value["Transfer Receipt"]
		for value in positions.values()
		if "Transfer Dispatch" in value and "Transfer Receipt" in value
	):
		frappe.throw(_("Transfer dispatch stops must precede their receiving stops."))
	return assignments


def validate_task_trip(doc, loads):
	if not any(is_demand_load(load) for load in loads):
		frappe.throw(_("Task-based trips require at least one warehouse-transfer load."))
	stored = (
		frappe.get_all(
			"Transport Trip Task",
			filters={"delivery_trip": doc.name},
			fields=[
				"task_key",
				"task_type",
				"address",
				"delivery_note",
				"transport_load",
				"transport_demand",
				"delivery_stop",
				"sequence",
			],
		)
		if doc.docstatus == 1
		else []
	)
	assignments = map_tasks_to_stops(stored or plan_tasks(loads), doc.delivery_stops)
	if doc.docstatus == 1 and frappe.db.exists("Transport Trip Task", {"delivery_trip": doc.name}):
		existing = {
			(row.task_key, row.delivery_stop, row.sequence)
			for row in frappe.get_all(
				"Transport Trip Task",
				filters={"delivery_trip": doc.name},
				fields=["task_key", "delivery_stop", "sequence"],
			)
		}
		if existing != {(spec["task_key"], stop.name, stop.idx) for spec, stop in assignments}:
			frappe.throw(
				_("Submitted trip task assignments are immutable. Cancel and amend the trip to replan.")
			)
	for spec, stop in assignments:
		stop.vuna_delivery_load = stop.vuna_delivery_load or spec["transport_load"]
		if spec.get("delivery_note"):
			note = frappe.get_doc("Delivery Note", spec["delivery_note"])
			if note.company != doc.company or (
				note.delivery_trip
				and note.delivery_trip != doc.name
				and frappe.db.get_value("Delivery Trip", note.delivery_trip, "docstatus") != 2
			):
				frappe.throw(_("Customer Delivery Note belongs to another company or active trip."))


def sync_task_plan(doc, method=None):
	if not is_task_trip(doc) or doc.docstatus == 2:
		return
	if doc.docstatus == 1 and frappe.db.exists("Transport Trip Task", {"delivery_trip": doc.name}):
		# Freeze the planning snapshot once submitted; address-master changes must
		# not silently rewrite an active journey or its cargo.
		frappe.db.set_value(
			"Transport Trip Task",
			{"delivery_trip": doc.name, "status": "Planned"},
			"status",
			"Ready",
			update_modified=False,
		)
		return
	from vuna_transport.services.execution.lineage_service import get_delivery_trip_loads

	assignments = map_tasks_to_stops(plan_tasks(get_delivery_trip_loads(doc)), doc.delivery_stops)
	active = set()
	for spec, stop in assignments:
		name = frappe.db.get_value(
			"Transport Trip Task", {"delivery_trip": doc.name, "task_key": spec["task_key"]}, "name"
		)
		task = frappe.get_doc("Transport Trip Task", name) if name else frappe.new_doc("Transport Trip Task")
		task.flags.from_trip_planner = True
		task.company = doc.company
		for field in (
			"task_type",
			"transport_load",
			"transport_demand",
			"delivery_note",
			"address",
			"task_key",
		):
			task.set(field, spec.get(field))
		task.delivery_trip, task.delivery_stop, task.sequence = doc.name, stop.name, stop.idx
		if not name:
			task.status = "Ready" if doc.docstatus == 1 else "Planned"
		elif doc.docstatus == 1 and task.status == "Planned":
			task.status = "Ready"
		task.set("items", spec["items"])
		if spec["task_type"] in ("Transfer Dispatch", "Transfer Receipt"):
			from vuna_transport.services.execution.allocation_transfer_service import allocation_stock_entry

			task.dispatch_stock_entry = allocation_stock_entry(
				doc.vuna_current_vehicle_allocation or doc.vuna_vehicle_allocation, spec["transport_load"]
			)
		task.save(ignore_permissions=True)
		active.add(task.name)
	for name in frappe.get_all("Transport Trip Task", filters={"delivery_trip": doc.name}, pluck="name"):
		if name not in active:
			frappe.delete_doc("Transport Trip Task", name, ignore_permissions=True)


def sync_task_execution(doc, method=None):
	trip_name = doc.name if doc.doctype == "Delivery Trip" else doc.delivery_trip
	trip = doc if doc.doctype == "Delivery Trip" else frappe.get_doc("Delivery Trip", trip_name)
	if not is_task_trip(trip) or trip.docstatus != 1:
		return
	tasks = frappe.get_all(
		"Transport Trip Task",
		filters={"delivery_trip": trip_name},
		fields=["name", "delivery_stop", "delivery_note", "task_type", "status"],
	)
	if not tasks:
		return
	for task in tasks:
		if task.task_type in ("Transfer Dispatch", "Transfer Receipt"):
			from vuna_transport.services.execution.transfer_proof_service import derive_transfer_status

			task.status = derive_transfer_status(frappe.get_doc("Transport Trip Task", task.name))
			frappe.db.set_value(
				"Transport Trip Task", task.name, "status", task.status, update_modified=False
			)
		if task.task_type == "Customer Delivery":
			pod = frappe.db.get_value(
				"Proof of Delivery",
				{
					"delivery_trip": trip_name,
					"delivery_stop": task.delivery_stop,
					"delivery_note": task.delivery_note,
					"docstatus": 1,
				},
				"status",
			)
			task.status = (
				"Failed"
				if pod in ("Failed", "Rejected")
				else "Completed with Variance"
				if pod == "Partially Delivered"
				else "Completed"
				if pod
				else "Ready"
			)
			frappe.db.set_value(
				"Transport Trip Task", task.name, "status", task.status, update_modified=False
			)
	for stop in trip.delivery_stops:
		assigned = [task for task in tasks if task.delivery_stop == stop.name]
		stop.visited = int(bool(assigned) and all(task.status in TERMINAL_TASK_STATUSES for task in assigned))
		values = {"visited": stop.visited}
		if not stop.visited:
			values["vuna_actual_departure"] = None
		frappe.db.set_value("Delivery Stop", stop.name, values, update_modified=False)
	from vuna_transport.services.execution.dispatch_guard_service import set_delivery_load_status

	memberships = defaultdict(set)
	for row in frappe.db.sql(
		"""select distinct t.name as task_name, li.parent as load_name
		from `tabTransport Trip Task` t join `tabTransport Trip Task Item` ti on ti.parent = t.name
		join `tabTransport Load Item` li on li.name = ti.load_item where t.delivery_trip = %s""",
		trip_name,
		as_dict=True,
	):
		memberships[row.load_name].add(row.task_name)
	for load_name, task_names in memberships.items():
		assigned = [task for task in tasks if task.name in task_names]
		current = frappe.db.get_value("Transport Load", load_name, "status")
		if current in ("Dispatched", "Completed", "Completed with Exceptions"):
			target = (
				"Completed"
				if assigned and all(task.status in TERMINAL_TASK_STATUSES for task in assigned)
				else "Dispatched"
			)
			if target == "Completed" and any(task.status != "Completed" for task in assigned):
				target = "Completed with Exceptions"
			set_delivery_load_status(load_name, target, _("Transport task progress updated"), force=True)
	complete = all(stop.visited for stop in trip.delivery_stops)
	status = "Completed" if complete else "In Transit" if trip.vuna_actual_start else "Scheduled"
	values = {"status": status}
	if not complete:
		values["vuna_actual_completion"] = None
	frappe.db.set_value("Delivery Trip", trip_name, values, update_modified=False)
	trip.status = status
	if not complete:
		trip.vuna_actual_completion = None
	if doc.doctype != "Delivery Trip" and trip.vuna_current_vehicle_allocation:
		from vuna_transport.services.fleet.vehicle_allocation_service import sync_allocation_execution_status

		sync_allocation_execution_status(trip.vuna_current_vehicle_allocation)


def cancel_tasks(doc, method=None):
	if is_task_trip(doc):
		frappe.db.set_value(
			"Transport Trip Task", {"delivery_trip": doc.name}, "status", "Cancelled", update_modified=False
		)


def remove_draft_tasks(doc, method=None):
	if is_task_trip(doc) and doc.docstatus == 0:
		for name in frappe.get_all("Transport Trip Task", filters={"delivery_trip": doc.name}, pluck="name"):
			frappe.delete_doc("Transport Trip Task", name, ignore_permissions=True)


def require_tasks_complete(trip):
	if is_task_trip(trip) and (
		not frappe.db.exists("Transport Trip Task", {"delivery_trip": trip.name})
		or frappe.db.exists(
			"Transport Trip Task",
			{"delivery_trip": trip.name, "status": ["not in", list(TERMINAL_TASK_STATUSES)]},
		)
	):
		frappe.throw(
			_("Complete every transport task through its execution proof before completing this trip.")
		)


def guard_task_source_cancellation(doc, method=None):
	field = {
		"Delivery Note": "delivery_note",
		"Transport Load": "transport_load",
		"Transport Demand": "transport_demand",
	}.get(doc.doctype)
	if not field:
		return
	if frappe.db.exists("Transport Trip Task", {field: doc.name, "status": ["!=", "Cancelled"]}):
		frappe.throw(_("Cancel the linked Delivery Trip before cancelling this transport source."))
	doc.ignore_linked_doctypes = list(
		set(getattr(doc, "ignore_linked_doctypes", []) or []) | {"Transport Trip Task"}
	)
