"""Diversion eligibility, plan approval and reservations; no stock posting or trip edits."""

import math

import frappe
from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
from erpnext.utilities.transaction_base import validate_uom_is_integer
from frappe import _
from frappe.utils import cint, flt

from vuna_transport.services.demand.source_policy_service import get_source_policy
from vuna_transport.services.execution.allocation_transfer_service import resolve_vehicle_transit_warehouse
from vuna_transport.services.execution.direct_return_pickup_service import _require_dispatcher
from vuna_transport.services.execution.return_service import get_pod_quantity_in_stock_uom
from vuna_transport.services.shared.security_service import (
	require_doctype_permission,
	require_document_permission,
)


@frappe.whitelist()
def get_shipment_diversion_context(return_request: str) -> dict:
	"""Explain whether a dispatcher can plan diversion of a customer rejection.

	This is a read-only foundation, not permission to dispatch. Later phases must
	reserve the selected quantities and verify stock and quality before execution.
	"""
	return _get_shipment_diversion_context(return_request)


def _get_shipment_diversion_context(return_request, for_update=False, allow_resolved=False):
	def read(doctype, name):
		return frappe.get_doc(doctype, name, for_update=True) if for_update else frappe.get_doc(doctype, name)

	request = read("Return Request", return_request)
	_require_dispatcher(request)
	if (
		request.docstatus != 1
		or request.status == "Cancelled"
		or (not allow_resolved and request.status in ("Closed", "Resolved"))
	):
		frappe.throw(_("Select an active, submitted Return Request."))
	if request.get("return_context") == "Depot" or not request.delivery_note or not request.proof_of_delivery:
		frappe.throw(
			_(
				"Shipment diversion starts from a customer delivery rejection linked to its POD and Delivery Note."
			)
		)
	approval = get_source_policy("Return Request").get("required_workflow_state")
	if approval and request.get("workflow_state") != approval:
		frappe.throw(_("The Return Request has not reached its configured approval state."))

	pod = read("Proof of Delivery", request.proof_of_delivery)
	note = read("Delivery Note", request.delivery_note)
	for document in (pod, note):
		require_document_permission(document, "read")
	if (
		pod.docstatus != 1
		or pod.status not in ("Partially Delivered", "Rejected", "Failed")
		or pod.get("proof_type") not in (None, "", "Customer Delivery")
		or pod.delivery_note != note.name
		or pod.delivery_trip != request.delivery_trip
		or pod.company != request.company
		or pod.customer != request.customer
		or note.docstatus != 1
		or note.is_return
		or note.company != request.company
		or note.customer != request.customer
		or (pod.get("return_request") and pod.return_request != request.name)
	):
		frappe.throw(
			_("The rejection POD, Delivery Note and Return Request must describe the same customer delivery.")
		)
	trip = read("Delivery Trip", request.delivery_trip)
	require_document_permission(trip, "write")
	if trip.company != request.company:
		frappe.throw(_("The original trip must belong to the Return Request company."))
	warehouse = resolve_vehicle_transit_warehouse(trip) if trip.vehicle else None
	blockers = []

	def block(code, message):
		blockers.append({"code": code, "message": _(message)})

	if trip.docstatus != 1 or trip.status in ("Completed", "Cancelled") or trip.get("vuna_actual_completion"):
		block(
			"trip_not_active",
			"The original trip is not active. A controlled trip-resumption decision is required.",
		)
	if not trip.vehicle or not trip.driver:
		block("missing_vehicle_or_driver", "The trip needs an assigned vehicle and driver.")
	events = request.get("custody_events") or []
	if (
		request.get("custody") != "Still With Vehicle"
		or not events
		or events[-1].get("to_custody") != "Still With Vehicle"
	):
		block(
			"custody_unconfirmed",
			"Confirm and record that the requested goods are still on the original vehicle before diverting them.",
		)
	if any(row.get("custody_warehouse") and row.custody_warehouse != warehouse for row in request.items):
		block(
			"different_custody_location",
			"Some requested goods were recorded at another custody location. Review their current location first.",
		)

	# Existing pickup/receiving work needs explicit reconciliation, not a second movement.
	if frappe.db.exists(
		"Transport Trip Task", {"return_request": request.name, "status": ["!=", "Cancelled"]}
	):
		block(
			"existing_return_task",
			"This return already has pickup or receiving work. Reconcile it before planning another movement.",
		)
	if frappe.db.exists("Return Inspection", {"return_request": request.name, "docstatus": ["!=", 2]}):
		block(
			"existing_inspection",
			"Receiving or inspection has already started for this return. Review that work before diverting goods.",
		)

	items = diversion_item_candidates(request, pod, note)
	if not items:
		block("no_rejected_quantity", "No requested, rejected goods remain in this source snapshot.")
	stock_return = None
	if request.get("return_stock_document"):
		stock_return = read("Delivery Note", request.return_stock_document)
		require_document_permission(stock_return, "read")
		if (
			not stock_return.is_return
			or stock_return.return_against != note.name
			or stock_return.company != request.company
			or stock_return.customer != request.customer
			or stock_return.get("vuna_return_request") != request.name
		):
			frappe.throw(
				_("The linked Sales Return must belong to this Return Request and original Delivery Note.")
			)
		if stock_return.docstatus != 2 and any(row.warehouse != warehouse for row in stock_return.items):
			block(
				"sales_return_location_mismatch",
				"The Sales Return uses a different receiving warehouse. Reconcile it with actual custody; do not receive the goods twice.",
			)

	return {
		"phase": "Eligibility Preview",
		"preview_only": True,
		"execution_enabled": False,
		"return_request": request.name,
		"company": request.company,
		"customer": request.customer,
		"proof_of_delivery": pod.name,
		"delivery_note": note.name,
		"delivery_trip": trip.name,
		"vehicle": trip.vehicle,
		"driver": trip.driver,
		"vehicle_warehouse": warehouse,
		"recorded_custody": request.get("custody"),
		"items": items,
		"sales_return": stock_return.name if stock_return else None,
		"sales_return_docstatus": stock_return.docstatus if stock_return else None,
		"can_plan_diversion": not blockers,
		"blockers": blockers,
		"stock_verified": False,
		"quantities_reserved": False,
		"customer_redelivery_clearance": "Not assessed",
	}


def diversion_item_candidates(request, pod, note):
	"""Source quantity bounds, not a claim that the goods are available stock."""
	sources = {row.name: row for row in note.items}
	proof_rows = {}
	for row in pod.items:
		if row.delivery_note_item in proof_rows:
			frappe.throw(
				_("The rejection contains repeated source rows; review its stock identity before diversion.")
			)
		proof_rows[row.delivery_note_item] = row
	seen = set()
	result = []
	for row in request.items:
		source = sources.get(row.delivery_note_item)
		proof = proof_rows.get(row.delivery_note_item)
		if not source or not proof or row.delivery_note_item in seen:
			frappe.throw(
				_("Each diversion candidate must identify one distinct received delivery item and POD row.")
			)
		seen.add(row.delivery_note_item)
		factor = flt(row.conversion_factor)
		from vuna_transport.services.execution.return_uom_service import (
			approved_return_uom,
			return_conversion_factor,
		)

		approved = approved_return_uom(row)
		valid_factor = (
			flt(approved.conversion_factor) if approved else return_conversion_factor(source, row.uom)
		)
		if (approved and row.uom != approved.uom) or factor != valid_factor:
			frappe.throw(_("Return item UOM conversion does not match its source or Item configuration."))
		dispatched_stock_qty = flt(source.stock_qty)
		quantity = flt(row.requested_return_qty)
		if (
			not math.isfinite(factor)
			or factor <= 0
			or not math.isfinite(quantity)
			or quantity < 0
			or row.item_code != source.item_code
			or proof.item_code != source.item_code
			or row.stock_uom != source.stock_uom
			or not math.isfinite(dispatched_stock_qty)
			or dispatched_stock_qty <= 0
			or (proof.get("uom") and proof.uom != source.uom)
			or (
				proof.get("conversion_factor")
				and flt(proof.conversion_factor) != flt(source.conversion_factor)
			)
			or (proof.get("batch_no") and row.get("batch_no") and proof.batch_no != row.batch_no)
		):
			frappe.throw(
				_(
					"Return items must retain finite quantities, a positive conversion factor and the original delivery item and stock UOM."
				)
			)
		rejected_stock_qty = get_pod_quantity_in_stock_uom(proof, source, "rejected_qty")
		requested_stock_qty = quantity * factor
		if (
			not math.isfinite(rejected_stock_qty)
			or rejected_stock_qty < 0
			or not math.isfinite(requested_stock_qty)
		):
			frappe.throw(_("Rejected and requested stock quantities must be finite and non-negative."))
		if requested_stock_qty > min(rejected_stock_qty, dispatched_stock_qty) + 1e-9:
			frappe.throw(
				_("Requested diversion quantities cannot exceed the original rejected delivery quantities.")
			)
		if requested_stock_qty <= 0:
			continue
		result.append(
			{
				"return_request_item": row.name,
				"delivery_note_item": source.name,
				"proof_of_delivery_item": proof.name,
				"item_code": source.item_code,
				"candidate_quantity": quantity,
				"uom": row.uom,
				"stock_qty": requested_stock_qty,
				"source_rejected_stock_qty": min(rejected_stock_qty, dispatched_stock_qty),
				"stock_uom": source.stock_uom,
				"conversion_factor": factor,
				"batch_no": proof.get("batch_no") or row.get("batch_no"),
				"rejected_serial_no": proof.get("rejected_serial_no"),
				"source_serial_and_batch_bundle": source.get("serial_and_batch_bundle"),
				"rejection_reason": proof.get("rejection_reason") or row.get("return_reason"),
			}
		)
	return result


def diversion_reservations(proof_of_delivery, exclude=None):
	"""Draft and approved plans consume one shared rejection envelope across requests."""
	return frappe.db.sql(
		"""select i.return_request_item, i.proof_of_delivery_item, i.stock_qty, i.serial_no
		from `tabShipment Diversion Item` i join `tabShipment Diversion` d on d.name=i.parent
		where d.proof_of_delivery=%s and d.docstatus in (0,1) and d.name!=%s
		and coalesce(d.previous_diversion,'')=''
		and i.parenttype='Shipment Diversion' for update""",
		(proof_of_delivery, exclude or ""),
		as_dict=True,
	)


def available_diversion_stock(item, reservations):
	request_reserved = proof_reserved = 0
	serials = set()
	for row in reservations:
		if row.return_request_item == item["return_request_item"]:
			request_reserved += flt(row.stock_qty)
		if row.proof_of_delivery_item == item["proof_of_delivery_item"]:
			proof_reserved += flt(row.stock_qty)
			serials.update(get_serial_nos(row.serial_no or ""))
	return max(
		min(item["stock_qty"] - request_reserved, item["source_rejected_stock_qty"] - proof_reserved), 0
	), serials


def _lock_diversion_source(return_request):
	request = frappe.get_doc("Return Request", return_request)
	_require_dispatcher(request)
	# Match rejection creation's POD -> Return Request order. All diversion writers
	# serialize on the original POD, including different requests for its rows.
	if request.proof_of_delivery:
		frappe.db.get_value("Proof of Delivery", request.proof_of_delivery, "name", for_update=True)
	frappe.db.get_value("Return Request", request.name, "name", for_update=True)


def _diversion_context_checks(context, custody_confirmed=False, submitting=False):
	if context["recorded_custody"] not in (None, "", "Unknown", "Still With Vehicle"):
		frappe.throw(
			_("Recorded custody places these goods elsewhere. Reconcile the handover before diversion.")
		)
	for issue in context["blockers"]:
		if issue["code"] == "custody_unconfirmed" and (not submitting or custody_confirmed):
			continue
		if issue["code"] == "trip_not_active" and not submitting:
			continue
		frappe.throw(issue["message"])
	if submitting and not custody_confirmed:
		frappe.throw(
			_("Confirm that all selected goods are still on this vehicle before approving the diversion.")
		)


def validate_diversion_destination(doc, original_customer):
	if doc.destination_type == "Customer":
		if doc.get("destination_warehouse"):
			frappe.throw(_("A customer diversion cannot also select a receiving depot warehouse."))
		if not doc.customer or not doc.delivery_address:
			if doc.docstatus == 1:
				frappe.throw(_("Select the new customer and delivery address before approval."))
			return
		customer = frappe.get_doc("Customer", doc.customer)
		address = frappe.get_doc("Address", doc.delivery_address)
		for target in (customer, address):
			require_document_permission(target, "read")
		if customer.get("disabled") or address.get("disabled") or customer.name == original_customer:
			frappe.throw(_("Select an enabled, different customer and a valid delivery address."))
		if not any(
			row.link_doctype == "Customer" and row.link_name == customer.name for row in address.links
		):
			frappe.throw(_("The delivery address must belong to the new customer."))
	elif doc.destination_type == "Depot":
		if doc.get("customer") or doc.get("delivery_address"):
			frappe.throw(_("A depot diversion cannot also select a customer or customer delivery address."))
		if not doc.destination_warehouse:
			if doc.docstatus == 1:
				frappe.throw(_("Select the receiving depot warehouse before approval."))
			return
		warehouse = frappe.get_doc("Warehouse", doc.destination_warehouse)
		require_document_permission(warehouse, "read")
		if (
			warehouse.company != doc.company
			or warehouse.is_group
			or warehouse.disabled
			or warehouse.warehouse_type == "Transit"
			or warehouse.name == doc.vehicle_warehouse
		):
			frappe.throw(
				_(
					"Select an active, non-group receiving warehouse in this company, not a vehicle transit warehouse."
				)
			)
		if warehouse.get("is_rejected_warehouse") and "nl_vms" in frappe.get_installed_apps():
			frappe.throw(_("Use a holding warehouse that does not automatically write off received stock."))
	else:
		frappe.throw(_("Choose Customer or Depot as the diversion destination."))


def validate_shipment_diversion(doc):
	_lock_diversion_source(doc.return_request)
	context = (
		_get_shipment_diversion_context(doc.return_request, for_update=True, allow_resolved=True)
		if doc.get("previous_diversion")
		else _get_shipment_diversion_context(doc.return_request, for_update=True)
	)
	previous = doc.get_doc_before_save()
	if previous and previous.return_request != doc.return_request:
		frappe.throw(_("A saved diversion cannot be moved to a different Return Request."))
	if previous and any(
		previous.get(field) != doc.get(field)
		for field in ("previous_diversion", "previous_proof_of_delivery")
	):
		frappe.throw(_("A saved diversion cannot change its preceding diversion or rejection proof."))
	for field in (
		"company",
		"proof_of_delivery",
		"delivery_note",
		"delivery_trip",
		"vehicle",
		"driver",
		"vehicle_warehouse",
	):
		if previous and previous.get(field) != context[field]:
			frappe.throw(
				_(
					"The original trip or stock context has changed. Cancel this plan and prepare a new diversion."
				)
			)
		doc.set(field, context[field])
	doc.original_customer = context["customer"]
	if doc.docstatus == 0:
		doc.sales_return = context["sales_return"]
	_diversion_context_checks(context, bool(cint(doc.custody_confirmed)), submitting=doc.docstatus == 1)
	doc.eligibility_notes = "\n".join(
		issue["message"]
		for issue in context["blockers"]
		if not (issue["code"] == "custody_unconfirmed" and doc.custody_confirmed)
	)
	if not (doc.reason or "").strip():
		frappe.throw(_("Enter a reason for the diversion."))
	validate_diversion_destination(doc, context["customer"])
	if not doc.items or len(doc.items) > 500:
		frappe.throw(_("Select between 1 and 500 diversion items."))
	reservations = diversion_reservations(context["proof_of_delivery"], doc.name)
	candidates = {row["return_request_item"]: row for row in context["items"]}
	if doc.get("previous_diversion"):
		from vuna_transport.services.execution.shipment_diversion_followup import (
			followup_candidates,
			root_diversion,
		)

		candidates, reservations = followup_candidates(doc, context)
		doc.sales_return = root_diversion(doc).get("sales_return")
	seen = set()
	for row in doc.items:
		item = candidates.get(row.return_request_item)
		if not item or row.return_request_item in seen:
			frappe.throw(_("Select distinct rejected Return Request items for this diversion."))
		seen.add(row.return_request_item)
		available, reserved_serials = available_diversion_stock(item, reservations)
		raw_qty = flt(row.qty)
		if not math.isfinite(raw_qty):
			frappe.throw(_("Diversion quantities must be finite."))
		qty = flt(raw_qty, row.precision("qty"))
		stock_qty = flt(qty * item["conversion_factor"], row.precision("stock_qty"))
		if (
			not math.isfinite(qty)
			or qty <= 0
			or not math.isfinite(stock_qty)
			or stock_qty <= 0
			or flt(stock_qty - available, row.precision("stock_qty")) > 0
		):
			frappe.throw(
				_("Item {0} exceeds its uncommitted rejected quantity, or has an invalid quantity.").format(
					item["item_code"]
				)
			)
		for field in (
			"item_code",
			"uom",
			"stock_uom",
			"conversion_factor",
			"batch_no",
			"delivery_note_item",
			"proof_of_delivery_item",
			"source_serial_and_batch_bundle",
		):
			row.set(field, item.get(field))
		for field in ("source_diversion_item", "origin_diversion_item"):
			row.set(field, item.get(field))
		row.qty, row.stock_qty, row.available_qty = qty, stock_qty, available / item["conversion_factor"]
		allowed = set(get_serial_nos(item.get("rejected_serial_no") or "")) - reserved_serials
		row.available_serial_nos = "\n".join(sorted(allowed))
		selected = get_serial_nos(row.serial_no or "")
		serialized = frappe.get_cached_value("Item", row.item_code, "has_serial_no")
		if selected and (
			not serialized
			or len(selected) != len(set(selected))
			or not set(selected).issubset(allowed)
			or len(selected) != stock_qty
		):
			frappe.throw(
				_("Select unique, uncommitted rejected serial numbers matching the diverted stock quantity.")
			)
		if doc.docstatus == 1 and serialized and len(selected) != stock_qty:
			frappe.throw(_("Select the exact rejected serial numbers before approval."))
	validate_uom_is_integer(doc, "uom", "qty", "Shipment Diversion Item")
	validate_uom_is_integer(doc, "stock_uom", "stock_qty", "Shipment Diversion Item")


@frappe.whitelist()
def make_shipment_diversion(source_name: str) -> dict:
	"""Open a populated, unsaved plan; only saving it reserves any quantities."""
	require_doctype_permission("Shipment Diversion", "create")
	context = get_shipment_diversion_context(source_name)
	_diversion_context_checks(context)
	reservations = diversion_reservations(context["proof_of_delivery"])
	doc = frappe.new_doc("Shipment Diversion")
	doc.return_request = source_name
	for field in (
		"company",
		"proof_of_delivery",
		"delivery_note",
		"delivery_trip",
		"vehicle",
		"driver",
		"vehicle_warehouse",
	):
		doc.set(field, context[field])
	doc.original_customer, doc.sales_return = context["customer"], context["sales_return"]
	doc.eligibility_notes = "\n".join(issue["message"] for issue in context["blockers"])
	for item in context["items"]:
		available, reserved_serials = available_diversion_stock(item, reservations)
		if available <= 0:
			continue
		allowed = set(get_serial_nos(item.get("rejected_serial_no") or "")) - reserved_serials
		doc.append(
			"items",
			{
				**{
					key: item.get(key)
					for key in (
						"return_request_item",
						"delivery_note_item",
						"proof_of_delivery_item",
						"item_code",
						"uom",
						"stock_uom",
						"conversion_factor",
						"batch_no",
						"source_serial_and_batch_bundle",
					)
				},
				"qty": available / item["conversion_factor"],
				"available_qty": available / item["conversion_factor"],
				"stock_qty": available,
				"available_serial_nos": "\n".join(sorted(allowed)),
				"serial_no": "\n".join(sorted(allowed)) if len(allowed) == available else "",
			},
		)
	if not doc.items:
		frappe.throw(_("All rejected quantities are already committed to diversion plans."))
	return doc.as_dict()
