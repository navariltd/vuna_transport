"""Dispatcher-assigned approved return work at existing trip stops, without planning documents."""

import frappe
from frappe import _
from frappe.utils import flt

from vuna_transport.services.shared.security_service import require_document_permission

TERMINAL = ("Completed", "Completed with Variance", "Failed", "Cancelled")


def _approval(request, active=True):
	from vuna_transport.services.demand.source_policy_service import get_source_policy

	if request.docstatus != 1 or (active and request.status in ("Closed", "Cancelled", "Resolved")):
		frappe.throw(_("Select an active, submitted Return Request."))
	if not active:
		return
	state = get_source_policy("Return Request").required_workflow_state
	if state and request.get("workflow_state") != state:
		frappe.throw(_("The Return Request has not reached the configured approval state."))


def _require_dispatcher(request):
	require_document_permission(request, "write")
	if not set(frappe.get_roles()) & {
		"System Manager",
		"Transport Manager",
		"Transport Dispatcher",
		"Transport Planner",
	}:
		frappe.throw(_("Only dispatch staff can assign return pickups."), frappe.PermissionError)


def _dispatcher(request, trip):
	_require_dispatcher(request)
	require_document_permission(trip, "write")


def _active_trip(request, trip):
	return (
		trip.docstatus == 1
		and trip.status not in ("Completed", "Cancelled")
		and not trip.vuna_actual_completion
		and bool(trip.driver)
		and trip.company == request.company
	)


def _source_warehouse(request):
	warehouses = {
		row.custody_warehouse or request.source_warehouse
		for row in request.items
		if flt(row.requested_return_qty) > 0
	}
	if len(warehouses) != 1 or not next(iter(warehouses)):
		frappe.throw(_("Direct depot pickup requires one known source warehouse."))
	warehouse = frappe.get_doc("Warehouse", next(iter(warehouses)))
	if (
		warehouse.company != request.company
		or warehouse.disabled
		or warehouse.is_group
		or warehouse.warehouse_type == "Transit"
	):
		frappe.throw(_("These returns must be held at an active depot warehouse, not already on a vehicle."))
	return warehouse.name


def _receiving_warehouse(request):
	if not request.return_warehouse:
		frappe.throw(_("Select the receiving Return Warehouse before assigning the depot pickup."))
	warehouse = frappe.get_doc("Warehouse", request.return_warehouse)
	if (
		warehouse.company != request.company
		or warehouse.disabled
		or warehouse.is_group
		or warehouse.warehouse_type == "Transit"
	):
		frappe.throw(_("Choose an active same-company receiving warehouse, not vehicle transit."))
	if warehouse.get("is_rejected_warehouse") and "nl_vms" in frappe.get_installed_apps():
		frappe.throw(_("Use a holding/quarantine return warehouse, not an automatic write-off warehouse."))
	return warehouse.name


def validate_stop(request, trip, stop_name):
	from vuna_transport.services.execution.collection_reference_service import stop_context

	context = stop_context(trip, stop_name)
	if request.return_context != "Depot":
		if not request.customer or request.customer != context["customer"]:
			frappe.throw(_("The pickup stop must belong to the Return Request customer."))
	else:
		if not _matches_depot(context, _source_warehouse(request)):
			frappe.throw(_("The pickup stop must identify the depot holding these returned goods."))
	return frappe.get_doc("Delivery Stop", stop_name)


def _matches_depot(context, source_warehouse):
	allowed = set(context["warehouses"])
	for warehouse in list(allowed):
		if holding := frappe.db.get_value("Warehouse", warehouse, "vuna_returns_warehouse"):
			allowed.add(holding)
	return source_warehouse in allowed


def _matching_stops(request, trip):
	from vuna_transport.services.execution.collection_reference_service import stop_context

	source = _source_warehouse(request) if request.return_context == "Depot" else None
	result = []
	for stop in trip.delivery_stops:
		if stop.visited:
			continue
		if request.return_context == "Depot":
			if not _matches_depot(stop_context(trip, stop.name, allow_empty=True), source):
				continue
		elif not request.customer or stop.customer != request.customer:
			continue
		party = source or stop.customer or stop.address
		label = f"{stop.idx}. {party}"
		if source and stop.address:
			label += f" — {stop.address}"
		result.append({"value": stop.name, "label": label})
	return result


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_assignment_trip_query(
	doctype: str, txt: str, searchfield: str, start: int, page_len: int, filters: dict | str | None
) -> list:
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	if not filters.get("return_request"):
		return []
	request = frappe.get_doc("Return Request", filters["return_request"])
	_require_dispatcher(request)
	_approval(request)
	# Permission-aware candidate search. Pagination is applied AFTER matching
	# stops, so unrelated trips cannot hide eligible later trips from the user.
	candidates = frappe.get_list(
		"Delivery Trip",
		filters={
			"company": request.company,
			"docstatus": 1,
			"status": ["not in", ["Completed", "Cancelled"]],
			"driver": ["is", "set"],
			"name": ["like", f"%{txt}%"],
		},
		pluck="name",
		limit=0,
		order_by="creation desc",
	)
	result = []
	for name in candidates:
		trip = frappe.get_doc("Delivery Trip", name)
		if _active_trip(request, trip) and trip.has_permission("write") and _matching_stops(request, trip):
			result.append((trip.name, trip.driver))
	return result[max(int(start), 0) : max(int(start), 0) + max(int(page_len), 1)]


def collected_quantities(request):
	return dict(
		frappe.db.sql(
			"""select i.source_row, sum(i.stock_qty)
		from `tabProof of Delivery Item` i join `tabProof of Delivery` p on p.name=i.parent
		where p.docstatus=1 and p.proof_type='Return Pickup' and p.return_request=%s
		group by i.source_row""",
			request.name,
		)
	)


def remaining_items(request):
	collected = collected_quantities(request)
	result = []
	for row in request.items:
		factor = flt(row.conversion_factor)
		if factor <= 0:
			frappe.throw(_("Return items require a positive UOM conversion factor."))
		remaining = max(
			flt(row.requested_return_qty) * factor
			- max(flt(collected.get(row.name)), flt(row.received_qty) * factor),
			0,
		)
		if flt(remaining, frappe.get_precision("Stock Entry Detail", "transfer_qty")) > 0:
			result.append(
				{
					"return_request_item": row.name,
					"item_code": row.item_code,
					"qty": remaining / factor,
					"uom": row.uom,
					"stock_uom": row.stock_uom,
					"conversion_factor": factor,
					"stock_qty": remaining,
				}
			)
	return result


@frappe.whitelist()
def get_assignment_stops(return_request: str, delivery_trip: str):
	request = frappe.get_doc("Return Request", return_request)
	trip = frappe.get_doc("Delivery Trip", delivery_trip)
	_dispatcher(request, trip)
	_approval(request)
	if not _active_trip(request, trip):
		frappe.throw(_("Choose an active submitted trip with a driver in the Return Request company."))
	return _matching_stops(request, trip)


@frappe.whitelist()
def assign_return_pickup(return_request: str, delivery_trip: str, delivery_stop: str):
	# Lock order matches execution: trip first, then source return case.
	frappe.db.get_value("Delivery Trip", delivery_trip, "name", for_update=True)
	frappe.db.get_value("Return Request", return_request, "name", for_update=True)
	trip = frappe.get_doc("Delivery Trip", delivery_trip)
	request = frappe.get_doc("Return Request", return_request)
	_dispatcher(request, trip)
	_approval(request)
	if not _active_trip(request, trip):
		frappe.throw(_("Choose an active submitted trip with a driver in the Return Request company."))
	if existing := frappe.db.get_value(
		"Transport Trip Task",
		{
			"return_request": request.name,
			"delivery_trip": trip.name,
			"delivery_stop": delivery_stop,
			"status": ["!=", "Cancelled"],
		},
		"name",
	):
		return existing
	if frappe.db.exists(
		"Transport Trip Task", {"return_request": request.name, "status": ["not in", list(TERMINAL)]}
	):
		frappe.throw(_("This Return Request already has an active direct pickup assignment."))
	if frappe.db.exists(
		"Transport Demand",
		{"source_document_type": "Return Request", "source_document": request.name, "docstatus": ["!=", 2]},
	):
		frappe.throw(_("Release the existing pickup demand before assigning this Return Request directly."))
	if (
		request.proof_of_delivery
		and frappe.db.get_value("Proof of Delivery", request.proof_of_delivery, "proof_type")
		== "Unplanned Collection"
	):
		frappe.throw(
			_("These goods were already collected; complete receiving instead of creating another pickup.")
		)
	stop = validate_stop(request, trip, delivery_stop)
	if stop.visited:
		frappe.throw(_("Choose a stop that the driver has not completed."))
	items = remaining_items(request)
	if not items:
		frappe.throw(_("This Return Request has no outstanding collection quantity."))
	sources = {row.name: row for row in request.items}
	for item in items:
		source = sources[item["return_request_item"]]
		has_batch, has_serial = frappe.db.get_value(
			"Item", source.item_code, ["has_batch_no", "has_serial_no"]
		)
		if (has_batch and not source.batch_no) or (has_serial and not source.serial_no):
			frappe.throw(
				_("Record the approved return batch/serial identities before assigning this pickup.")
			)
	if request.return_context == "Depot":
		_receiving_warehouse(request)
		from vuna_transport.services.execution.allocation_transfer_service import (
			resolve_vehicle_transit_warehouse,
		)

		resolve_vehicle_transit_warehouse(trip, check_permissions=False)
	task = frappe.new_doc("Transport Trip Task")
	task.update(
		{
			"return_request": request.name,
			"company": trip.company,
			"delivery_trip": trip.name,
			"delivery_stop": stop.name,
			"address": stop.address,
			"sequence": stop.idx,
			"task_type": "Return Pickup",
			"status": "Ready",
			"task_key": frappe.generate_hash(length=32),
		}
	)
	task.set("items", items)
	task.flags.from_trip_planner = True
	task.insert(ignore_permissions=True)
	request.add_comment(
		"Info",
		_("Pickup assigned to trip {0}, stop {1}, driver {2}.").format(trip.name, stop.idx, trip.driver),
	)
	return task.name


@frappe.whitelist()
def remove_return_pickup(task_name: str):
	trip_name = frappe.db.get_value("Transport Trip Task", task_name, "delivery_trip")
	frappe.db.get_value("Delivery Trip", trip_name, "name", for_update=True)
	task = frappe.get_doc("Transport Trip Task", task_name)
	if not task.return_request:
		frappe.throw(_("Only direct return assignments can be removed here."))
	request = frappe.get_doc("Return Request", task.return_request)
	_dispatcher(request, frappe.get_doc("Delivery Trip", task.delivery_trip))
	frappe.db.get_value("Return Request", request.name, "name", for_update=True)
	if frappe.db.exists("Proof of Delivery", {"transport_trip_task": task.name, "docstatus": ["!=", 2]}):
		frappe.throw(
			_("Cancel/delete the pickup POD and its stock transaction before removing this assignment.")
		)
	task.db_set("status", "Cancelled")
	from vuna_transport.services.execution.trip_task_service import sync_task_execution

	sync_task_execution(task)
	request.add_comment("Info", _("Direct pickup assignment {0} removed.").format(task.name))
	return task.name


def direct_context(task):
	trip = frappe.get_doc("Delivery Trip", task.delivery_trip)
	request = frappe.get_doc("Return Request", task.return_request)
	_approval(request, active=False)
	if (
		task.status == "Cancelled"
		or task.task_type != "Return Pickup"
		or trip.docstatus != 1
		or len({task.company, trip.company, request.company}) != 1
		or task.transport_load
		or task.transport_demand
	):
		frappe.throw(_("Direct pickup must retain its approved Return Request, trip and stop."))
	validate_stop(request, trip, task.delivery_stop)
	return trip, None, request, None


def direct_items(task):
	_trip, _demand, request, _load = direct_context(task)
	sources = {row.name: row for row in request.items}
	result = []
	for cargo in task.items:
		source = sources.get(cargo.return_request_item)
		if (
			not source
			or source.item_code != cargo.item_code
			or source.uom != cargo.uom
			or flt(source.conversion_factor) != flt(cargo.conversion_factor)
		):
			frappe.throw(_("Pickup task items must retain their approved return item references."))
		result.append(
			{
				"source_row": source.name,
				"item_code": source.item_code,
				"item_name": source.item_name,
				"ordered_qty": cargo.qty,
				"delivered_qty": cargo.qty,
				"rejected_qty": 0,
				"uom": cargo.uom,
				"stock_uom": cargo.stock_uom,
				"conversion_factor": cargo.conversion_factor,
				"stock_qty": cargo.stock_qty,
				"batch_no": source.batch_no,
				"serial_no": source.serial_no or "",
				"source_bundle": source.source_bundle,
			}
		)
	return result


def guard_return_cancellation(request):
	if frappe.db.exists(
		"Transport Trip Task", {"return_request": request.name, "status": ["!=", "Cancelled"]}
	):
		frappe.throw(_("Remove the direct pickup assignment before cancelling this Return Request."))


def validate_direct_receipt(entry):
	if not entry.get("outgoing_stock_entry"):
		return
	proof_name = frappe.db.get_value("Stock Entry", entry.outgoing_stock_entry, "vuna_return_pickup_proof")
	if not proof_name:
		return
	proof = frappe.get_doc("Proof of Delivery", proof_name)
	task = frappe.get_doc("Transport Trip Task", proof.transport_trip_task)
	if not task.return_request and entry.get("vuna_transport_proof"):
		# Planned receiving PODs have their own exact receipt-group validation.
		return
	from vuna_transport.services.execution.transfer_proof_service import stock_identities

	request = frappe.get_doc("Return Request", task.return_request or proof.return_request)
	stock = frappe.get_doc("Stock Entry", entry.outgoing_stock_entry)
	_receiving_warehouse(request)
	if (
		stock.docstatus != 1
		or proof.docstatus != 1
		or request.docstatus != 1
		or entry.company != request.company
		or entry.purpose != "Material Transfer"
		or entry.add_to_transit
	):
		frappe.throw(_("End Transit must receive the approved direct return pickup."))
	sources = {row.name: row for row in stock.items}
	for row in entry.items:
		source = sources.get(row.ste_detail)
		if (
			not source
			or row.item_code != source.item_code
			or row.uom != source.uom
			or flt(row.conversion_factor) != flt(source.conversion_factor)
			or row.s_warehouse != stock.to_warehouse
			or row.t_warehouse != request.return_warehouse
		):
			frappe.throw(
				_(
					"Receive the pickup stock into its Return Request receiving warehouse, preserving source rows and UOM."
				)
			)
		allowed, actual = stock_identities(source), stock_identities(row)
		if not set(actual).issubset(allowed) or any(
			not set(value["serials"]).issubset(set(allowed[batch]["serials"]))
			or flt(value["stock_qty"]) > flt(allowed[batch]["stock_qty"])
			for batch, value in actual.items()
		):
			frappe.throw(_("End Transit must preserve the collected batch/serial identities."))
