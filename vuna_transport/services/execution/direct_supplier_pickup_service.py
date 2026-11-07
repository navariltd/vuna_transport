"""Manual supplier demand assignment. No new load, allocation or inventory posting."""

import frappe
from frappe import _
from frappe.utils import flt

from vuna_transport.services.shared.security_service import require_document_permission


def require_dispatcher(demand, trip):
	for doc in (demand, trip):
		require_document_permission(doc, "write")
	if not set(frappe.get_roles()) & {
		"System Manager",
		"Transport Manager",
		"Transport Planner",
		"Transport Dispatcher",
	}:
		frappe.throw(_("Only dispatch staff can assign transport demands."), frappe.PermissionError)


def validate_assignment_context(demand, trip):
	from vuna_transport.services.demand.delivery_method_service import get_delivery_method_workflow
	from vuna_transport.services.demand.source_policy_service import require_source_enabled
	from vuna_transport.services.demand.supplier_pickup_demand_service import build_supplier_pickup_legs

	if (
		demand.docstatus != 1
		or demand.movement_type != "Supplier Pickup"
		or demand.source_document_type != "Purchase Order"
	):
		frappe.throw(_("Select a submitted supplier pickup demand."))
	if trip.docstatus != 1 or trip.status in ("Completed", "Cancelled") or trip.vuna_actual_completion:
		frappe.throw(_("Select an active, submitted Delivery Trip."))
	if (
		trip.company != demand.company
		or not (trip.vuna_current_driver or trip.driver)
		or not (trip.vuna_current_vehicle or trip.vehicle)
	):
		frappe.throw(_("The trip must have a vehicle and driver in the demand's company."))
	allocation_name = trip.vuna_current_vehicle_allocation or trip.vuna_vehicle_allocation
	if not allocation_name:
		frappe.throw(_("Choose a trip with an existing Vehicle Allocation for supplier vehicle receipts."))
	allocation = frappe.get_doc("Vehicle Allocation", allocation_name)
	require_document_permission(allocation, "read")
	if (
		allocation.docstatus != 1
		or allocation.company != trip.company
		or allocation.status in ("Completed", "Cancelled", "Replaced")
		or allocation.vehicle != (trip.vuna_current_vehicle or trip.vehicle)
		or allocation.driver != (trip.vuna_current_driver or trip.driver)
	):
		frappe.throw(_("The trip's current allocation must match its active vehicle and driver."))
	order = frappe.get_doc("Purchase Order", demand.source_document)
	require_document_permission(order, "read")
	require_source_enabled(order)
	workflow = get_delivery_method_workflow(order.vuna_delivery_method)
	if workflow.transport_arranged_by != "Company" or workflow.requires_transport_planning:
		frappe.throw(
			_(
				"Use a company-arranged Delivery Method with the transport workflow options off for direct trip assignment."
			)
		)
	leg = next(
		(value for value in build_supplier_pickup_legs(order) if value["source_key"] == demand.source_key),
		None,
	)
	if (
		not leg
		or leg["company"] != demand.company
		or leg["source_address"] != demand.source_address
		or leg["destination_warehouse"] != demand.destination_warehouse
	):
		frappe.throw(
			_(
				"The supplier demand no longer matches its Purchase Order. Review the source before assignment."
			)
		)
	available = {row["source_item"]: row for row in leg["items"]}
	from vuna_transport.services.execution.supplier_vehicle_receipt_service import (
		reserved_purchase_quantities,
	)

	reserved = reserved_purchase_quantities(order.name)
	for row in demand.items:
		source = available.get(row.source_item)
		if (
			not source
			or row.item_code != source["item_code"]
			or row.transaction_uom != source["transaction_uom"]
			or flt(row.conversion_factor) != flt(source["conversion_factor"])
			or flt(row.transaction_qty) <= 0
			or flt(row.transaction_qty) > flt(source["transaction_qty"]) - flt(reserved.get(row.source_item))
		):
			frappe.throw(_("Demand quantities or UOM no longer match the outstanding Purchase Order."))
	if not demand.items:
		frappe.throw(_("The demand has no outstanding items."))
	return order


def demand_capacity(demand):
	from vuna_transport.services.demand.delivery_order_service import (
		_get_group_transport_requirements,
		_get_item_transport_measurements,
	)
	from vuna_transport.services.shared.measurement_service import get_transport_uoms

	uoms = get_transport_uoms()
	values = frappe._dict(
		name=demand.name,
		weight_uom=uoms.weight_uom,
		volume_uom=uoms.volume_uom,
		total_weight=0,
		total_volume=0,
		total_pallets=0,
		total_cases=0,
		total_crates=0,
		delivery_orders=[frappe._dict(row.as_dict(), delivery_order=None) for row in demand.items],
	)
	values.update(
		_get_group_transport_requirements(
			[(row, row.transaction_qty) for row in demand.items], company=demand.company
		)
	)
	for row in demand.items:
		measurements = _get_item_transport_measurements(row, row.transaction_qty)
		values.total_weight += measurements.weight
		values.total_volume += measurements.volume
	return values


def validate_trip_capacity(trip, demand):
	"""Conservative whole-trip capacity: never assume earlier deliveries free space."""
	from vuna_transport.services.execution.lineage_service import get_delivery_trip_loads
	from vuna_transport.services.fleet.vehicle_allocation_service import _build_combined_capacity_load
	from vuna_transport.services.planning.delivery_load_service import validate_capacity
	from vuna_transport.services.planning.load_validation_service import (
		validate_transport_compatibility_groups,
	)
	from vuna_transport.services.shared.measurement_service import convert_measurement, get_transport_uoms

	loads = get_delivery_trip_loads(trip)
	assigned = frappe.get_all(
		"Transport Trip Task",
		filters={"delivery_trip": trip.name, "direct_assignment": 1, "status": ["!=", "Cancelled"]},
		pluck="transport_demand",
	)
	loads += [
		demand_capacity(frappe.get_doc("Transport Demand", name))
		for name in sorted(set(assigned))
		if name and name != demand.name
	]
	loads.append(demand_capacity(demand))
	uoms = get_transport_uoms()
	# Normalize copies, never rewrite historical load measurements.
	loads = [
		frappe._dict(load.as_dict()) if callable(getattr(load, "as_dict", None)) else frappe._dict(load)
		for load in loads
	]
	for load in loads:
		load.total_weight = convert_measurement(
			load.total_weight, load.weight_uom, uoms.weight_uom, _("weight")
		)
		load.total_volume = convert_measurement(
			load.total_volume, load.volume_uom, uoms.volume_uom, _("volume")
		)
		load.weight_uom, load.volume_uom = uoms.weight_uom, uoms.volume_uom
	combined = _build_combined_capacity_load(loads)
	validate_transport_compatibility_groups(combined.delivery_orders)
	category = frappe.db.get_value(
		"Vehicle", trip.vuna_current_vehicle or trip.vehicle, "vuna_vehicle_category"
	)
	if not category:
		frappe.throw(_("Set the trip vehicle's Vehicle Category before assigning more cargo."))
	validate_capacity(combined, category)


@frappe.whitelist()
def assign_supplier_demand(transport_demand: str, delivery_trip: str) -> str:
	# Trip -> PO -> demand follows the collection/receipt locking order.
	frappe.db.get_value("Delivery Trip", delivery_trip, "name", for_update=True)
	demand = frappe.get_doc("Transport Demand", transport_demand)
	trip = frappe.get_doc("Delivery Trip", delivery_trip)
	require_dispatcher(demand, trip)
	frappe.db.get_value("Purchase Order", demand.source_document, "name", for_update=True)
	frappe.db.get_value("Transport Demand", demand.name, "name", for_update=True)
	demand.reload()
	active = frappe.db.get_value(
		"Transport Trip Task",
		{"transport_demand": demand.name, "direct_assignment": 1, "status": ["!=", "Cancelled"]},
		["name", "delivery_trip"],
		as_dict=True,
	)
	if active:
		if active.delivery_trip == trip.name:
			return active.name
		frappe.throw(_("This demand is already assigned to another trip. Remove that assignment first."))
	validate_assignment_context(demand, trip)
	if frappe.db.sql(
		"""select i.name from `tabTransport Load Item` i join `tabTransport Load` l on l.name=i.parent
		where i.transport_demand=%s and l.docstatus!=2 limit 1""",
		demand.name,
	):
		frappe.throw(_("Remove existing load commitments before assigning this demand directly."))
	validate_trip_capacity(trip, demand)
	from vuna_transport.services.execution.allocation_transfer_service import (
		resolve_vehicle_transit_warehouse,
	)

	resolve_vehicle_transit_warehouse(
		frappe._dict(company=trip.company, vehicle=trip.vuna_current_vehicle or trip.vehicle)
	)
	stop = next(
		(row for row in trip.delivery_stops if row.address == demand.source_address and not row.visited), None
	)
	if stop is None:
		stop = frappe.get_doc(
			{
				"doctype": "Delivery Stop",
				"parent": trip.name,
				"parenttype": "Delivery Trip",
				"parentfield": "delivery_stops",
				"idx": len(trip.delivery_stops) + 1,
				"address": demand.source_address,
				"visited": 0,
				"vuna_stop_type": "Supplier",
				"docstatus": 1,
			}
		)
		stop.insert(ignore_permissions=True)
	task = frappe.get_doc(
		{
			"doctype": "Transport Trip Task",
			"direct_assignment": 1,
			"transport_demand": demand.name,
			"company": demand.company,
			"delivery_trip": trip.name,
			"delivery_stop": stop.name,
			"address": stop.address,
			"sequence": stop.idx,
			"task_type": "Supplier Pickup",
			"status": "Ready",
			"task_key": frappe.generate_hash(length=32),
			"items": [
				{
					"transport_demand_item": row.name,
					"item_code": row.item_code,
					"qty": row.transaction_qty,
					"uom": row.transaction_uom,
					"conversion_factor": row.conversion_factor,
					"stock_qty": row.stock_qty,
					"stock_uom": row.stock_uom,
				}
				for row in demand.items
			],
		}
	)
	task.flags.from_trip_planner = True
	task.insert(ignore_permissions=True)
	from vuna_transport.services.planning.demand_lifecycle_service import sync_demand_allocations

	sync_demand_allocations([demand.name])
	demand.add_comment(
		"Info", _("Supplier pickup assigned to trip {0}, stop {1}.").format(trip.name, stop.idx)
	)
	return task.name


@frappe.whitelist()
def remove_supplier_assignment(task_name: str) -> str:
	from vuna_transport.services.execution.transfer_execution_service import _lock_task
	from vuna_transport.services.execution.trip_task_service import sync_task_execution
	from vuna_transport.services.planning.demand_lifecycle_service import sync_demand_allocations

	task = _lock_task(task_name)
	if not task.direct_assignment or task.task_type != "Supplier Pickup":
		frappe.throw(_("Select a directly assigned supplier pickup task."))
	demand, trip = (
		frappe.get_doc("Transport Demand", task.transport_demand),
		frappe.get_doc("Delivery Trip", task.delivery_trip),
	)
	require_dispatcher(demand, trip)
	if frappe.db.exists("Purchase Receipt", {"vuna_pickup_task": task.name, "docstatus": ["!=", 2]}):
		frappe.throw(_("Cancel or delete the linked Purchase Receipt before removing this assignment."))
	task.db_set("status", "Cancelled")
	if not frappe.db.exists(
		"Transport Trip Task",
		{"delivery_trip": trip.name, "delivery_stop": task.delivery_stop, "status": ["!=", "Cancelled"]},
	):
		stop = frappe.get_doc("Delivery Stop", task.delivery_stop)
		if not stop.delivery_note and stop.vuna_stop_type == "Supplier":
			stop.db_set("visited", 1)
	sync_demand_allocations([demand.name])
	sync_task_execution(task)
	return task.name


def supplier_task_status(task):
	receipt = frappe.db.get_value(
		"Purchase Receipt",
		{"vuna_pickup_task": task.name, "docstatus": ["!=", 2]},
		["name", "docstatus"],
		as_dict=True,
	)
	if not receipt:
		return "Ready"
	if receipt.docstatus == 0:
		return "Awaiting Stock Posting"
	quantities = {
		row.purchase_order_item: flt(row.qty)
		for row in frappe.get_doc("Purchase Receipt", receipt.name).items
	}
	sources = {
		row.name: row.source_item for row in frappe.get_doc("Transport Demand", task.transport_demand).items
	}
	return (
		"Completed"
		if all(quantities.get(sources.get(row.transport_demand_item)) == flt(row.qty) for row in task.items)
		else "Completed with Variance"
	)


def sync_supplier_receipt(doc, method=None):
	if not doc.get("vuna_pickup_task"):
		return
	from vuna_transport.services.execution.trip_task_service import sync_task_execution

	sync_task_execution(frappe.get_doc("Transport Trip Task", doc.vuna_pickup_task))


def receipt_task_limits(task, order, allocation):
	"""Keep procurement authority and PO/UOM lineage on the standard receipt."""
	trip = frappe.get_doc("Delivery Trip", task.delivery_trip)
	demand = frappe.get_doc("Transport Demand", task.transport_demand)
	if (
		not task.direct_assignment
		or task.task_type != "Supplier Pickup"
		or task.status == "Cancelled"
		or trip.docstatus != 1
		or trip.status in ("Completed", "Cancelled")
	):
		frappe.throw(_("The supplier pickup task is no longer active."))
	if (
		demand.source_document != order.name
		or demand.company != order.company
		or trip.company != order.company
		or (trip.vuna_current_vehicle_allocation or trip.vuna_vehicle_allocation) != allocation.name
	):
		frappe.throw(
			_("The Purchase Receipt must use its pickup task's Purchase Order and current trip allocation.")
		)
	if not trip.vuna_actual_start:
		frappe.throw(_("Start the trip before confirming supplier collection."))
	sources = {row.name: row for row in demand.items}
	limits = {}
	for row in task.items:
		source = sources.get(row.transport_demand_item)
		if (
			not source
			or row.item_code != source.item_code
			or row.uom != source.transaction_uom
			or flt(row.conversion_factor) != flt(source.conversion_factor)
		):
			frappe.throw(_("Pickup task items must retain their demand and Purchase Order references."))
		limits[source.source_item] = flt(row.qty)
	return limits


@frappe.whitelist()
def create_supplier_receipt(task_name: str) -> str:
	from vuna_transport.services.execution.supplier_vehicle_receipt_service import (
		create_vehicle_purchase_receipt,
	)
	from vuna_transport.services.execution.transfer_execution_service import _lock_task

	task = _lock_task(task_name)
	require_document_permission(task, "read")
	trip = frappe.get_doc("Delivery Trip", task.delivery_trip)
	demand = frappe.get_doc("Transport Demand", task.transport_demand)
	return create_vehicle_purchase_receipt(
		demand.source_document,
		trip.vuna_current_vehicle_allocation or trip.vuna_vehicle_allocation,
		task_name=task.name,
	)


def guard_trip_cancellation(doc, method=None):
	tasks = frappe.get_all(
		"Transport Trip Task", filters={"delivery_trip": doc.name, "direct_assignment": 1}, pluck="name"
	)
	if tasks and frappe.db.exists(
		"Purchase Receipt", {"vuna_pickup_task": ["in", tasks], "docstatus": ["!=", 2]}
	):
		frappe.throw(_("Cancel or delete supplier pickup Purchase Receipts before cancelling this trip."))
