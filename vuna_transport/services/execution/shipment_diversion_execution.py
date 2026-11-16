"""Dispatcher-controlled diversion stops and ordinary driver POD completion."""

from html import escape

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, now_datetime

from vuna_transport.services.execution.shipment_diversion_documents import (
	check_customer_quality,
	check_vehicle_stock,
	document_context,
	existing_document,
	posted_return,
)
from vuna_transport.services.shared.security_service import require_document_permission


def _trip(doc):
	trip = frappe.get_doc("Delivery Trip", doc.delivery_trip, for_update=True)
	require_document_permission(trip, "write")
	if (
		trip.docstatus != 1
		or trip.company != doc.company
		or (trip.get("vuna_current_vehicle") or trip.vehicle) != doc.vehicle
		or (trip.get("vuna_current_driver") or trip.driver) != doc.driver
	):
		frappe.throw(_("The original trip must still use the approved company, vehicle and driver."))
	return trip


@frappe.whitelist()
def resume_original_trip(shipment_diversion: str) -> str:
	from vuna_transport.services.execution.shipment_diversion_service import (
		_diversion_context_checks,
		_get_shipment_diversion_context,
		_lock_diversion_source,
	)
	from vuna_transport.services.fleet.driver_availability_service import evaluate_driver_availability
	from vuna_transport.services.fleet.vehicle_availability_service import evaluate_vehicle_availability

	doc = frappe.get_doc("Shipment Diversion", shipment_diversion)
	require_document_permission(doc, "write")
	_lock_diversion_source(doc.return_request)
	doc = frappe.get_doc("Shipment Diversion", doc.name, for_update=True)
	if doc.get("execution_task") or doc.get("status") in ("Completed", "Needs Review"):
		frappe.throw(
			_(
				"This diversion already has execution history. Review that work instead of resuming the original trip again."
			)
		)
	if doc.docstatus == 2 or not cint(doc.custody_confirmed) or not (doc.reason or "").strip():
		frappe.throw(
			_("Save the diversion reason and confirm that the selected goods are on this vehicle first.")
		)
	context = (
		_get_shipment_diversion_context(doc.return_request, for_update=True, allow_resolved=True)
		if doc.get("previous_diversion")
		else _get_shipment_diversion_context(doc.return_request, for_update=True)
	)
	_diversion_context_checks(context, custody_confirmed=True)
	trip = _trip(doc)
	if trip.status == "Cancelled":
		frappe.throw(_("A cancelled trip cannot be resumed."))
	if trip.status != "Completed" and not trip.get("vuna_actual_completion"):
		return trip.name
	# Completed trips have released their resources. Recheck current operational restrictions.
	for doctype, name in (("Vehicle", doc.vehicle), ("Driver", doc.driver)):
		frappe.db.get_value(doctype, name, "name", for_update=True)
	if frappe.get_all(
		"Delivery Trip",
		filters={"name": ["!=", trip.name], "docstatus": 1, "status": ["not in", ["Completed", "Cancelled"]]},
		or_filters={
			"vehicle": doc.vehicle,
			"vuna_current_vehicle": doc.vehicle,
			"driver": doc.driver,
			"vuna_current_driver": doc.driver,
		},
		pluck="name",
		limit_page_length=1,
	):
		frappe.throw(_("The vehicle or driver already has another active trip. Do not resume this journey."))
	start = now_datetime()
	end = add_to_date(start, hours=1)
	allocation = trip.get("vuna_current_vehicle_allocation") or trip.get("vuna_vehicle_allocation")
	for result in (
		evaluate_vehicle_availability(doc.vehicle, start, end, exclude_allocation=allocation),
		evaluate_driver_availability(
			doc.driver, start, end, exclude_allocation=allocation, expected_company=doc.company
		),
	):
		if not result.dispatch_eligible:
			frappe.throw(
				_("Resolve the vehicle or driver's availability restrictions before resuming this trip.")
			)
	previous_completion = trip.get("vuna_actual_completion")
	trip.db_set(
		{
			"status": "In Transit" if trip.get("vuna_actual_start") else "Scheduled",
			"vuna_actual_completion": None,
		},
		update_modified=True,
	)
	doc.db_set({"trip_resumed_on": start, "trip_resumed_by": frappe.session.user}, update_modified=True)
	trip.add_comment(
		"Info",
		_("Resumed explicitly for diversion {0}; previous completion: {1}.").format(
			doc.name, previous_completion or ""
		),
	)
	return trip.name


@frappe.whitelist()
def add_diversion_to_trip(shipment_diversion: str) -> str:
	from vuna_transport.services.routing.location_service import get_warehouse_planning_data

	preview = frappe.get_doc("Shipment Diversion", shipment_diversion)
	require_document_permission(preview, "write")
	if preview.docstatus == 1 and (
		existing := frappe.db.get_value(
			"Transport Trip Task", {"shipment_diversion": preview.name, "status": ["!=", "Cancelled"]}, "name"
		)
	):
		return existing  # Reopening an existing assignment is safe even after its trip completes.
	doc = document_context(shipment_diversion)
	trip = _trip(doc)
	if trip.status in ("Completed", "Cancelled") or trip.get("vuna_actual_completion"):
		frappe.throw(_("Resume the original trip explicitly before adding this diversion."))
	if existing := frappe.db.get_value(
		"Transport Trip Task", {"shipment_diversion": doc.name, "status": ["!=", "Cancelled"]}, "name"
	):
		return existing
	receipt = posted_return(doc)
	if doc.destination_type == "Customer":
		target = existing_document(doc, "Delivery Note")
		if not target or target.docstatus != 1:
			frappe.throw(_("Submit the onward Delivery Note before adding the customer stop."))
		check_customer_quality(doc, receipt)
		address, kind = doc.delivery_address, "Customer Delivery"
	else:
		target = existing_document(doc, "Stock Entry")
		if not target or target.docstatus != 0:
			frappe.throw(
				_(
					"Create the draft depot Stock Entry before adding the receiving stop. Do not post receipt before arrival."
				)
			)
		check_vehicle_stock(doc)
		address = get_warehouse_planning_data(doc.destination_warehouse).get("address")
		kind = "Transfer Receipt"
	if not address:
		frappe.throw(_("Configure the receiving warehouse address before adding its delivery stop."))
	stop = frappe.get_doc(
		{
			"doctype": "Delivery Stop",
			"parent": trip.name,
			"parenttype": "Delivery Trip",
			"parentfield": "delivery_stops",
			"docstatus": 1,
			"idx": max((row.idx for row in trip.delivery_stops), default=0) + 1,
			"address": address,
			"visited": 0,
			"customer": doc.customer if kind == "Customer Delivery" else None,
			"delivery_note": target.name if kind == "Customer Delivery" else None,
			"vuna_stop_type": "Customer" if kind == "Customer Delivery" else "Depot",
			"vuna_depot_names": doc.destination_warehouse if kind == "Transfer Receipt" else None,
		}
	)
	stop.insert(ignore_permissions=True)
	task = frappe.get_doc(
		{
			"doctype": "Transport Trip Task",
			"shipment_diversion": doc.name,
			"company": doc.company,
			"delivery_trip": trip.name,
			"delivery_stop": stop.name,
			"address": address,
			"sequence": stop.idx,
			"task_type": kind,
			"status": "Ready",
			"task_key": "diversion:" + doc.name,
			"direct_assignment": 1,
			"delivery_note": target.name if kind == "Customer Delivery" else None,
			"dispatch_stock_entry": target.name if kind == "Transfer Receipt" else None,
			"items": [
				{
					"item_code": row.item_code,
					"qty": row.qty,
					"uom": row.uom,
					"conversion_factor": row.conversion_factor,
					"stock_qty": row.stock_qty,
					"stock_uom": row.stock_uom,
				}
				for row in doc.items
			],
		}
	)
	task.flags.from_trip_planner = True
	task.insert(ignore_permissions=True)
	doc.db_set(
		{"execution_task": task.name, "execution_stop": stop.name, "status": "In Transit"},
		update_modified=True,
	)
	trip.add_comment(
		"Info",
		_("Diversion {0} added as stop {1}; original stops and proof were preserved.").format(
			doc.name, stop.idx
		),
	)
	frappe.db.set_value(
		"Delivery Trip",
		trip.name,
		{"modified": now_datetime(), "modified_by": frappe.session.user},
		update_modified=False,
	)
	frappe.publish_realtime(
		"doc_update",
		{"doctype": "Delivery Trip", "name": trip.name},
		doctype="Delivery Trip",
		docname=trip.name,
		after_commit=True,
	)
	return task.name


def depot_context(task):
	if (
		not task.get("shipment_diversion")
		or task.task_type != "Transfer Receipt"
		or task.status == "Cancelled"
	):
		frappe.throw(_("Select an active depot diversion delivery task."))
	doc = frappe.get_doc("Shipment Diversion", task.shipment_diversion)
	trip = frappe.get_doc("Delivery Trip", task.delivery_trip)
	entry = frappe.get_doc("Stock Entry", task.dispatch_stock_entry, for_update=True)
	if (
		doc.docstatus != 1
		or doc.destination_type != "Depot"
		or doc.execution_task != task.name
		or doc.delivery_trip != trip.name
		or doc.execution_stop != task.delivery_stop
		or trip.docstatus != 1
		or (trip.get("vuna_current_vehicle") or trip.vehicle) != doc.vehicle
		or (trip.get("vuna_current_driver") or trip.driver) != doc.driver
		or entry.docstatus == 2
		or entry.get("vuna_shipment_diversion") != doc.name
	):
		frappe.throw(_("The diversion task no longer matches its approved trip and stock movement."))
	if (
		entry.company != doc.company
		or trip.company != doc.company
		or any(row.s_warehouse != doc.vehicle_warehouse for row in entry.items)
	):
		frappe.throw(_("The depot receipt must retain its approved company and movement endpoints."))
	return doc, trip, entry


def prepare_depot_proof(task):
	from vuna_transport.services.execution.shipment_diversion_exceptions import planned_stock
	from vuna_transport.services.execution.transfer_proof_service import planned_proof_items

	doc, trip, entry = depot_context(task)
	if existing := frappe.db.get_value(
		"Proof of Delivery", {"transport_trip_task": task.name, "docstatus": ["!=", 2]}, "name"
	):
		return frappe.get_doc("Proof of Delivery", existing)
	if entry.docstatus != 0 or trip.get("vuna_actual_completion"):
		frappe.throw(
			_("This delivery is already received or the trip has completed. Do not receive stock twice.")
		)
	proof = frappe.new_doc("Proof of Delivery")
	proof.update(
		{
			"transport_trip_task": task.name,
			"proof_type": "Transfer Receipt",
			"direction": "Delivery",
			"company": doc.company,
			"delivery_trip": trip.name,
			"delivery_stop": task.delivery_stop,
			"delivery_address": task.address,
			"dispatch_stock_entry": entry.name,
			"delivery_datetime": now_datetime(),
			"outcome": "Completed",
		}
	)
	proof.set("items", planned_proof_items(planned_stock(doc)))
	return proof


def validate_depot_proof(proof, task):
	from vuna_transport.services.execution.depot_custody_service import validate_depot_custody
	from vuna_transport.services.execution.shipment_diversion_exceptions import (
		normalize_source_rows,
		planned_stock,
	)
	from vuna_transport.services.execution.transfer_proof_service import validate_proof_items

	doc, trip, entry = depot_context(task)
	if entry.docstatus != 0 or trip.get("vuna_actual_completion"):
		frappe.throw(_("Stock is already received or the trip is complete; review the existing receipt."))
	if proof.docstatus == 1 and not trip.get("vuna_actual_start"):
		frappe.throw(_("Start the trip before recording delivery."))
	if frappe.db.exists(
		"Proof of Delivery",
		{"transport_trip_task": task.name, "docstatus": ["!=", 2], "name": ["!=", proof.name or ""]},
	):
		frappe.throw(_("This diversion task already has active proof."))
	proof.company, proof.delivery_trip, proof.delivery_stop = doc.company, trip.name, task.delivery_stop
	proof.proof_type, proof.direction = "Transfer Receipt", "Delivery"
	proof.dispatch_stock_entry, proof.delivery_address = entry.name, task.address
	normalize_source_rows(proof, doc, entry)
	stock = planned_stock(doc)
	proof.outcome = validate_proof_items(proof.items, stock)
	validate_depot_custody(proof, stock, doc.destination_warehouse)
	proof.total_accepted_qty = sum(flt(row.delivered_qty) for row in proof.items)
	proof.total_rejected_qty = sum(flt(row.rejected_qty) for row in proof.items)
	proof.status = (
		"Accepted"
		if proof.outcome == "Completed"
		else "Partially Delivered"
		if proof.total_accepted_qty
		else "Rejected"
		if proof.total_rejected_qty
		else "Failed"
	)
	if proof.docstatus == 1:
		if not (proof.received_by or "").strip():
			frappe.throw(_("Enter the name of the person confirming this handover."))
		if any(flt(row.missing_qty) > 0 for row in proof.items) and not (proof.remarks or "").strip():
			frappe.throw(_("Explain the missing or unaccounted quantity in Remarks."))
		proof.recorded_by, proof.delivery_datetime = frappe.session.user, now_datetime()


def post_depot_proof(proof):
	from vuna_transport.services.execution.driver_stock_policy import driver_end_transit_enabled
	from vuna_transport.services.execution.transfer_execution_service import _lock_task

	task = _lock_task(proof.transport_trip_task)
	doc, _trip_doc, entry = depot_context(task)
	if entry.docstatus != 0:
		frappe.throw(_("This diversion receipt is already posted; do not receive it twice."))
	from vuna_transport.services.execution.shipment_diversion_exceptions import (
		apply_receipt,
		create_rejection_request,
	)

	request = create_rejection_request(doc, proof)
	if not apply_receipt(entry, doc, proof, request):
		return  # A failed/full vehicle-held rejection has no stock receipt to post.
	entry.flags.ignore_permissions = True
	previous = frappe.flags.get("vuna_diversion_receipt_proof")
	try:
		frappe.flags.vuna_diversion_receipt_proof = proof.name
		entry.save(ignore_permissions=True)
		if driver_end_transit_enabled():
			entry.submit()
	finally:
		frappe.flags.vuna_diversion_receipt_proof = previous


def validate_delegated_receipt(entry, proof_name):
	"""Only the server's validated POD submission can delegate this exact stock receipt."""
	from vuna_transport.services.execution.shipment_diversion_exceptions import validate_receipt

	proof = frappe.get_doc("Proof of Delivery", proof_name)
	task = frappe.get_doc("Transport Trip Task", proof.transport_trip_task)
	doc, _trip_doc, saved = depot_context(task)
	if (
		proof.docstatus != 1
		or entry.name != saved.name
		or entry.get("vuna_shipment_diversion") != doc.name
		or entry.company != doc.company
		or entry.purpose != "Material Transfer"
		or entry.add_to_transit
		or entry.outgoing_stock_entry
	):
		frappe.throw(_("Driver posting is limited to the approved diversion's exact depot receipt."))
	validate_receipt(entry, doc, proof)
	if entry.docstatus != 1:
		return
	totals = {}
	for row in entry.items:
		totals[row.item_code] = totals.get(row.item_code, 0) + flt(row.transfer_qty)
	for item, qty in totals.items():
		available = frappe.db.get_value(
			"Bin", {"item_code": item, "warehouse": doc.vehicle_warehouse}, "actual_qty", for_update=True
		)
		if flt(available) + 1e-6 < qty:
			frappe.throw(
				_("The diverted quantity is no longer available on the vehicle. Stock was not posted.")
			)


@frappe.whitelist()
def remove_diversion_assignment(shipment_diversion: str) -> str:
	doc = frappe.get_doc("Shipment Diversion", shipment_diversion, for_update=True)
	require_document_permission(doc, "write")
	trip = _trip(doc)
	if not doc.get("execution_task"):
		return trip.name
	task = frappe.get_doc("Transport Trip Task", doc.execution_task, for_update=True)
	if task.get("shipment_diversion") != doc.name:
		frappe.throw(_("The task does not belong to this diversion."))
	if frappe.db.exists(
		"Proof of Delivery",
		{"delivery_trip": trip.name, "delivery_stop": task.delivery_stop, "docstatus": ["!=", 2]},
	):
		frappe.throw(_("Cancel and delete the stop's proof before removing this assignment."))
	if frappe.db.exists(
		"Transport Trip Task",
		{"delivery_stop": task.delivery_stop, "name": ["!=", task.name], "status": ["!=", "Cancelled"]},
	):
		frappe.throw(_("Other work now uses this stop. Review it before removing the diversion."))
	if (
		doc.destination_type == "Depot"
		and frappe.db.get_value("Stock Entry", task.dispatch_stock_entry, "docstatus") == 1
	):
		frappe.throw(_("Reverse the posted depot receipt before removing its delivery assignment."))
	stop = frappe.get_doc("Delivery Stop", task.delivery_stop)
	if stop.parent != trip.name or stop.parenttype != "Delivery Trip":
		frappe.throw(_("The assigned stop no longer belongs to this trip."))
	# Keep the complete recorded visit before removing its active routing row.
	task.add_comment("Info", "<pre>" + escape(frappe.as_json(stop.as_dict())) + "</pre>")
	task.db_set("status", "Cancelled", update_modified=True)
	frappe.db.delete(
		"Delivery Stop", {"name": task.delivery_stop, "parent": trip.name, "parenttype": "Delivery Trip"}
	)
	doc.db_set({"execution_task": None, "execution_stop": None, "status": "Approved"}, update_modified=True)
	trip.add_comment(
		"Info",
		_("Removed diversion {0} from stop {1}. Stock and sales documents were not reversed.").format(
			doc.name, task.sequence
		),
	)
	trip.reload()
	from vuna_transport.services.execution.trip_task_service import sync_task_execution

	sync_task_execution(trip)
	if trip.delivery_stops and all(cint(row.visited) for row in trip.delivery_stops):
		trip.db_set(
			{
				"status": "Completed",
				"vuna_actual_completion": trip.get("vuna_actual_completion") or now_datetime(),
			},
			update_modified=True,
		)
	return trip.name


def sync_diversion_execution(document, method=None):
	if document.doctype == "Proof of Delivery":
		name = (
			frappe.db.get_value(
				"Transport Trip Task", document.get("transport_trip_task"), "shipment_diversion"
			)
			if document.get("transport_trip_task")
			else frappe.db.get_value(
				"Delivery Note", document.get("delivery_note"), "vuna_shipment_diversion"
			)
			if document.get("delivery_note")
			else None
		)
	else:
		name = document.get("vuna_shipment_diversion")
	if not name:
		return
	doc = frappe.get_doc("Shipment Diversion", name)
	if not doc.get("execution_task"):
		return
	task = frappe.get_doc("Transport Trip Task", doc.execution_task)
	if doc.destination_type == "Depot":
		status = depot_task_status(task)
		task.db_set("status", status, update_modified=False)
	from vuna_transport.services.execution.trip_task_service import sync_task_execution

	sync_task_execution(frappe.get_doc("Delivery Trip", doc.delivery_trip))
	task.reload()
	doc.db_set(
		"status",
		"Completed"
		if task.status == "Completed"
		else "Needs Review"
		if task.status in ("Failed", "Completed with Variance")
		else "In Transit",
		update_modified=True,
	)
	from vuna_transport.services.execution.shipment_diversion_followup import sync_ancestor_requests

	sync_ancestor_requests(doc)


def guard_execution_removal(document, method=None):
	if document.get("doctype") == "Shipment Diversion":
		name = document.name
	elif document.get("doctype") == "Delivery Trip":
		if frappe.db.exists(
			"Transport Trip Task",
			{
				"delivery_trip": document.name,
				"shipment_diversion": ["is", "set"],
				"status": ["!=", "Cancelled"],
			},
		):
			frappe.throw(_("Resolve the assigned shipment diversions before cancelling this trip."))
		return
	else:
		name = document.get("vuna_shipment_diversion")
	if name and frappe.db.exists(
		"Transport Trip Task", {"shipment_diversion": name, "status": ["!=", "Cancelled"]}
	):
		frappe.throw(
			_("Remove the diversion's trip assignment before cancelling its plan or stock/sales documents.")
		)


def depot_task_status(task):
	from vuna_transport.services.execution.depot_custody_service import receipt_groups

	posted = frappe.db.get_value("Stock Entry", task.dispatch_stock_entry, "docstatus") == 1
	name = frappe.db.get_value(
		"Proof of Delivery", {"transport_trip_task": task.name, "docstatus": 1}, "name"
	)
	if not name:
		return "Completed" if posted else "Ready"
	proof = frappe.get_doc("Proof of Delivery", name)
	if not receipt_groups(proof):
		return "Failed"
	if not posted:
		return "Awaiting Stock Posting"
	return "Completed" if proof.outcome == "Completed" else "Completed with Variance"
