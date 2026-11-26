"""Unplanned collections are custody evidence, not stock or commercial authority."""

import hashlib
import json
import math
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, now_datetime

from vuna_transport.services.execution.unplanned_pickup_policy import (
	require_unplanned_pickups_enabled,
	unplanned_pickups_enabled,
)
from vuna_transport.services.shared.security_service import require_document_permission

PROOF_TYPE = "Unplanned Collection"


def _require_collection_stop(trip_name, stop_name):
	if not stop_name or not frappe.db.get_value(
		"Delivery Stop", {"name": stop_name, "parent": trip_name, "parenttype": "Delivery Trip"}, "name"
	):
		frappe.throw(_("Select a collection stop belonging to this Delivery Trip."))
	return stop_name


def validate_collection(proof):
	require_unplanned_pickups_enabled()
	frappe.db.get_value("Delivery Trip", proof.delivery_trip, "name", for_update=True)
	trip = frappe.get_doc("Delivery Trip", proof.delivery_trip)
	if not proof.flags.get("mobile_collection"):
		require_document_permission(trip)
	if trip.docstatus != 1 or not trip.vuna_actual_start:
		frappe.throw(_("Unplanned collections require a started, submitted trip."))
	if trip.vuna_actual_completion or trip.status == "Completed":
		frappe.throw(_("Cannot record a collection against a completed trip."))
	if proof.company != trip.company:
		frappe.throw(_("Collection company must match the trip."))
	_require_collection_stop(trip.name, proof.get("collection_stop"))
	from vuna_transport.services.execution.collection_reference_service import (
		reference_options,
		validate_reference_rows,
	)

	reference = reference_options(
		trip,
		proof.collection_stop,
		proof.get("collection_reference_doctype"),
		proof.get("collection_reference_name"),
		lock=True,
	)
	proof.collection_party_type = reference["party_type"]
	proof.collection_customer = reference["customer"] if reference["party_type"] == "Customer" else None
	proof.collection_warehouse = None
	proof.collection_location = reference["location"]
	if not (proof.collection_location or "").strip() or not (proof.notes or "").strip():
		frappe.throw(_("Record the collection location and reason."))
	if not (proof.received_by or "").strip():
		frappe.throw(_("Record who released the goods."))
	if len(proof.collection_location) > 1000 or len(proof.notes) > 4000 or len(proof.received_by) > 140:
		frappe.throw(
			_("Keep location within 1,000 characters, reason within 4,000, and contact name within 140.")
		)
	if not proof.items or len(proof.items) > 50:
		frappe.throw(_("Record between 1 and 50 collected item rows."))
	when = get_datetime(proof.delivery_datetime or now_datetime())
	if when > now_datetime() or when < get_datetime(trip.vuna_actual_start):
		frappe.throw(_("Collection time must fall between the trip start and now."))
	proof.delivery_datetime = when
	# These references must never turn a driver report into a planned/posted movement.
	for field in (
		"transport_trip_task",
		"delivery_stop",
		"delivery_load",
		"delivery_note",
		"dispatch_stock_entry",
	):
		if proof.get(field):
			frappe.throw(_("Unplanned collection evidence cannot include planned stock references."))
	if proof.return_request or proof.collection_review_status not in (None, "", "Pending Review"):
		frappe.throw(_("Only dispatch review may link a Return Request or review a collection."))
	seen_serials = set()
	for row in proof.items:
		source = next(
			(
				option
				for option in reference["items"]
				if option["reference_row"] == row.get("collection_reference_row")
				and (option["batch_no"] or "") == (row.batch_no or "")
			),
			None,
		)
		if not source:
			frappe.throw(_("Select an item row from the referenced Delivery Note or Stock Entry."))
		_normalize_item(row, seen_serials, source)
	validate_reference_rows(proof, reference)
	if proof.collection_warehouse:
		proof.collection_location = proof.collection_warehouse
	proof.direction = "Pickup"
	proof.status, proof.outcome = "Collected", "Completed"
	proof.total_accepted_qty = sum(flt(row.delivered_qty) for row in proof.items)
	proof.total_rejected_qty = 0
	proof.collection_review_status = "Pending Review"
	proof.recorded_by = frappe.session.user
	proof.collection_reported_at = now_datetime()
	proof.collection_driver = trip.driver
	proof.collection_vehicle = trip.vehicle
	proof.collection_reviewed_by = proof.collection_reviewed_at = proof.collection_review_notes = None


def _normalize_item(row, seen_serials, source=None):
	from erpnext.stock.get_item_details import get_conversion_factor

	from vuna_transport.services.execution.collection_reference_service import collection_conversion_factor

	item = frappe.get_doc("Item", row.item_code)
	if item.disabled or not item.is_stock_item:
		frappe.throw(_("Only active stock items may be collected."))
	quantity = flt(row.delivered_qty)
	if not math.isfinite(quantity) or quantity <= 0:
		frappe.throw(_("Collected quantity must be a finite positive number."))
	quantity = flt(quantity, frappe.get_precision("Proof of Delivery Item", "delivered_qty"))
	if quantity <= 0:
		frappe.throw(_("Collected quantity is below the configured quantity precision."))
	row.delivered_qty = quantity
	row.uom = row.uom or item.stock_uom
	if not source and row.uom != item.stock_uom and not any(detail.uom == row.uom for detail in item.uoms):
		frappe.throw(_("Select a configured Item UOM for {0}.").format(item.name))
	factor = flt(
		collection_conversion_factor(source, row.uom)
		if source
		else get_conversion_factor(item.name, row.uom).get("conversion_factor")
	)
	if not math.isfinite(factor) or factor <= 0:
		frappe.throw(_("Select a configured UOM for {0}.").format(item.name))
	row.item_name, row.stock_uom, row.conversion_factor = item.item_name, item.stock_uom, factor
	row.stock_qty = flt(quantity * factor, frappe.get_precision("Proof of Delivery Item", "stock_qty"))
	if not math.isfinite(row.stock_qty) or row.stock_qty <= 0:
		frappe.throw(_("Collected stock quantity must remain positive at the configured precision."))
	for uom, qty in ((row.uom, quantity), (row.stock_uom, row.stock_qty)):
		if frappe.db.get_value("UOM", uom, "must_be_whole_number") and qty != int(qty):
			frappe.throw(_("{0} requires whole-number quantities.").format(uom))
	if row.get("source_bundle") or row.get("source_row") or row.get("delivery_note_item"):
		frappe.throw(_("Collection evidence cannot reuse another transaction's stock rows or bundles."))
	if item.has_batch_no:
		if not row.batch_no or frappe.db.get_value("Batch", row.batch_no, "item") != item.name:
			frappe.throw(
				_("Record a valid batch for {0}; use separate rows for different batches.").format(item.name)
			)
	elif row.batch_no:
		frappe.throw(_("This item does not use batches."))
	serials = [value.strip() for value in (row.serial_no or "").splitlines() if value.strip()]
	if item.has_serial_no:
		if len(serials) != row.stock_qty or len(set(serials)) != len(serials):
			frappe.throw(_("Collected serial numbers must match the stock quantity exactly."))
		for serial in serials:
			identity = frappe.db.get_value("Serial No", serial, ["item_code", "batch_no"], as_dict=True)
			if (
				not identity
				or identity.item_code != item.name
				or (row.batch_no and identity.batch_no != row.batch_no)
			):
				frappe.throw(_("Collected serial number does not match its item and batch."))
			if serial in seen_serials:
				frappe.throw(_("A serial number cannot appear in multiple collection rows."))
			seen_serials.add(serial)
	elif serials:
		frappe.throw(_("This item does not use serial numbers."))
	row.serial_no = "\n".join(serials)
	row.ordered_qty = source["stock_qty"] / factor if source else quantity
	row.rejected_qty = row.rejected_stock_qty = row.missing_qty = 0
	row.rejected_serial_no = None


def report_collection(installation_id, trip_name, payload, device_identifier=None):
	from vuna_transport.services.mobile.mobile_trip_execution_service import (
		_lock_assigned_trip,
		_require_started_trip,
		_validate_request_id,
	)

	_driver, trip = _lock_assigned_trip(installation_id, trip_name, device_identifier)
	payload = frappe.parse_json(payload) if isinstance(payload, str) else payload
	if not isinstance(payload, dict):
		frappe.throw(_("Invalid collection payload."))
	request_id = _validate_request_id(payload.get("request_id"))
	digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
	existing = frappe.db.get_value(
		"Proof of Delivery",
		{"mobile_request_id": request_id},
		["name", "owner", "delivery_trip", "payload_hash", "docstatus"],
		as_dict=True,
	)
	if existing:
		if (
			existing.owner != frappe.session.user
			or existing.delivery_trip != trip.name
			or existing.payload_hash != digest
			or existing.docstatus != 1
		):
			frappe.throw(_("This collection request ID has already been used. Refresh before retrying."))
		return {"name": existing.name, "created": False}
	require_unplanned_pickups_enabled()
	_require_started_trip(trip)
	_require_collection_stop(trip.name, payload.get("stop_name"))
	proof = frappe.new_doc("Proof of Delivery")
	proof.update(
		{
			"proof_type": PROOF_TYPE,
			"company": trip.company,
			"delivery_trip": trip.name,
			"collection_stop": payload.get("stop_name"),
			"collection_reference_doctype": payload.get("reference_doctype"),
			"collection_reference_name": payload.get("reference_name"),
			"notes": payload.get("reason"),
			"received_by": payload.get("released_by"),
			"delivery_datetime": payload.get("collected_at"),
			"mobile_request_id": request_id,
			"payload_hash": digest,
		}
	)
	from vuna_transport.services.mobile.location_evidence import apply_device_location

	apply_device_location(proof, payload)
	rows = payload.get("items")
	if (
		not isinstance(rows, list)
		or not 1 <= len(rows) <= 50
		or any(not isinstance(row, dict) for row in rows)
	):
		frappe.throw(_("Record between 1 and 50 collected item rows."))
	for row in rows:
		proof.append(
			"items",
			{
				key: row.get(key)
				for key in (
					"item_code",
					"uom",
					"delivered_qty",
					"batch_no",
					"serial_no",
					"collection_reference_row",
				)
			},
		)
	proof.flags.mobile_collection = True
	proof.flags.ignore_permissions = True
	proof.insert(ignore_permissions=True)
	proof.submit()
	return {"name": proof.name, "created": True}


@frappe.whitelist()
def review_collection(proof_name: str, return_request: str, notes: str):
	"""Acknowledge evidence and link a real case; never post stock or authorize credit."""
	frappe.db.get_value("Proof of Delivery", proof_name, "name", for_update=True)
	proof = frappe.get_doc("Proof of Delivery", proof_name)
	_require_dispatch_review(proof)
	if not (notes or "").strip():
		frappe.throw(_("Record the review and stock-receipt follow-up instructions."))
	request = frappe.get_doc("Return Request", return_request)
	frappe.db.get_value("Return Request", request.name, "name", for_update=True)
	request.reload()
	require_document_permission(request)
	if request.docstatus == 2 or request.company != proof.company:
		frappe.throw(_("Select an active Return Request in the collection company."))
	if request.return_context != ("Depot" if proof.collection_party_type == "Depot" else "Customer"):
		frappe.throw(_("Return Request context must match the collection source."))
	if proof.collection_party_type == "Customer" and request.delivery_note != proof.collection_reference_name:
		frappe.throw(_("The Return Request must reference the collection's Delivery Note."))
	if request.return_context == "Depot":
		if frappe.db.exists(
			"Transport Demand",
			{
				"source_document_type": "Return Request",
				"source_document": request.name,
				"docstatus": ["!=", 2],
			},
		):
			frappe.throw(
				_("Resolve the planned pickup demand before linking an unplanned collection to this case.")
			)
		_collection_receipt_rows(proof, request)
	_validate_request_items(proof, request)
	if proof.collection_review_status == "Reviewed":
		if proof.return_request != request.name:
			frappe.throw(_("This collection has already been reviewed against another Return Request."))
		return proof.name
	proof.db_set(
		{
			"return_request": request.name,
			"collection_review_status": "Reviewed",
			"collection_review_notes": notes,
			"collection_reviewed_by": frappe.session.user,
			"collection_reviewed_at": now_datetime(),
		}
	)
	proof.add_comment(
		"Info",
		_("Collection reviewed against {0}. No stock or credit was posted. {1}").format(request.name, notes),
	)
	return proof.name


def _validate_request_items(proof, request):
	expected, collected = defaultdict(float), defaultdict(float)
	customer = proof.get("collection_party_type") == "Customer"
	for row in request.items:
		key = row.delivery_note_item if customer else (row.item_code, row.get("batch_no") or "")
		expected[key] += flt(row.requested_return_qty) * flt(row.conversion_factor or 1)
	for row in proof.items:
		key = row.collection_reference_row if customer else (row.item_code, row.batch_no or "")
		collected[key] += flt(row.stock_qty)
	for row in frappe.db.sql(
		"""select i.item_code, i.collection_reference_row, i.batch_no, sum(i.stock_qty) as qty
		from `tabProof of Delivery Item` i join `tabProof of Delivery` p on p.name=i.parent
		where p.docstatus=1 and p.proof_type=%s and p.collection_review_status='Reviewed'
		and p.return_request=%s and p.name!=%s group by i.item_code, i.collection_reference_row, i.batch_no""",
		(PROOF_TYPE, request.name, proof.name),
		as_dict=True,
	):
		key = row.collection_reference_row if customer else (row.item_code, row.batch_no or "")
		collected[key] += flt(row.qty)
	for key, qty in collected.items():
		if flt(qty - expected[key], frappe.get_precision("Proof of Delivery Item", "stock_qty")) > 0:
			frappe.throw(_("Collected item/batch quantity exceeds the linked Return Request expectation."))


def collection_setup(installation_id, trip_name, stop_name, device_identifier=None):
	from vuna_transport.services.execution.collection_reference_service import stop_context
	from vuna_transport.services.mobile.mobile_device_service import (
		_get_assigned_trip,
		_require_mobile_driver,
	)

	driver = _require_mobile_driver(installation_id, device_identifier)
	trip = _get_assigned_trip(trip_name, driver.name)
	_require_collection_stop(trip.name, stop_name)
	if not unplanned_pickups_enabled():
		return {"enabled": False, "reference_types": []}
	return {**stop_context(trip, stop_name), "enabled": True}


@frappe.whitelist()
def desk_collection_reference(
	trip_name: str, stop_name: str, reference_doctype: str = "", reference_name: str = ""
):
	from vuna_transport.services.execution.collection_reference_service import reference_options, stop_context

	trip = frappe.get_doc("Delivery Trip", trip_name)
	require_document_permission(trip)
	require_unplanned_pickups_enabled()
	if not reference_name:
		return stop_context(trip, stop_name)
	return reference_options(trip, stop_name, reference_doctype, reference_name)


def collection_catalog(
	installation_id, trip_name, stop_name, reference_doctype, reference_name, txt="", device_identifier=None
):
	from vuna_transport.services.mobile.mobile_trip_execution_service import (
		_lock_assigned_trip,
		_require_started_trip,
	)

	_driver, trip = _lock_assigned_trip(installation_id, trip_name, device_identifier)
	require_unplanned_pickups_enabled()
	_require_started_trip(trip)
	from vuna_transport.services.execution.collection_reference_service import reference_options

	items = reference_options(trip, stop_name, reference_doctype, reference_name)["items"]
	return [row for row in items if txt.lower() in f"{row['name']} {row['item_name']}".lower()]


def collection_history(installation_id, trip_name, stop_name, device_identifier=None):
	from vuna_transport.services.mobile.mobile_device_service import (
		_get_assigned_trip,
		_require_mobile_driver,
	)

	driver = _require_mobile_driver(installation_id, device_identifier)
	trip = _get_assigned_trip(trip_name, driver.name)
	_require_collection_stop(trip.name, stop_name)
	return frappe.get_all(
		"Proof of Delivery",
		filters={
			"delivery_trip": trip.name,
			"collection_stop": stop_name,
			"proof_type": PROOF_TYPE,
			"docstatus": 1,
		},
		fields=[
			"name",
			"collection_location",
			"delivery_datetime",
			"collection_review_status",
			"return_request",
		],
		order_by="creation desc",
		limit_page_length=100,
	)


def _require_dispatch_review(proof):
	require_document_permission(proof, "write")
	if not set(frappe.get_roles()) & {
		"System Manager",
		"Transport Manager",
		"Transport Dispatcher",
		"Transport Planner",
	}:
		frappe.throw(_("Dispatch review permission is required."), frappe.PermissionError)
	if proof.docstatus != 1 or proof.proof_type != PROOF_TYPE:
		frappe.throw(_("Select a submitted unplanned collection."))


@frappe.whitelist()
def create_depot_case(proof_name: str, source_warehouse: str, return_warehouse: str, notes: str):
	"""Dispatch resolves an unknown depot collection into an explicit draft case."""
	from vuna_transport.services.shared.security_service import require_doctype_permission

	frappe.db.get_value("Proof of Delivery", proof_name, "name", for_update=True)
	proof = frappe.get_doc("Proof of Delivery", proof_name)
	_require_dispatch_review(proof)
	require_doctype_permission("Return Request", "create")
	if proof.collection_party_type != "Depot":
		frappe.throw(_("Customer collections require a Return Request against the original sale."))
	if source_warehouse != proof.collection_warehouse:
		frappe.throw(_("Use the depot warehouse identified by the referenced Stock Entry."))
	if proof.return_request:
		return proof.return_request
	_validate_warehouses(proof.company, source_warehouse, return_warehouse)
	request = frappe.new_doc("Return Request")
	request.update(
		{
			"return_context": "Depot",
			"company": proof.company,
			"proof_of_delivery": proof.name,
			"source_warehouse": source_warehouse,
			"return_warehouse": return_warehouse,
			"return_source": "Manual",
			"initiated_by": "Dispatcher",
			"status": "Draft",
			"return_remarks": proof.notes,
		}
	)
	for row in proof.items:
		request.append(
			"items",
			{
				"source_pod_item": row.name,
				"custody_warehouse": source_warehouse,
				"item_code": row.item_code,
				"item_name": row.item_name,
				"requested_return_qty": row.delivered_qty,
				"delivered_qty": 0,
				"uom": row.uom,
				"stock_uom": row.stock_uom,
				"conversion_factor": row.conversion_factor,
				"batch_no": row.batch_no,
				"serial_no": row.serial_no,
				"return_reason": proof.notes,
			},
		)
	request.insert()
	review_collection(proof.name, request.name, notes)
	return request.name


def _validate_warehouses(company, source, destination):
	if not source or not destination or source == destination:
		frappe.throw(_("Choose distinct source and receiving warehouses."))
	for name in (source, destination):
		warehouse = frappe.get_doc("Warehouse", name)
		require_document_permission(warehouse)
		if (
			warehouse.company != company
			or warehouse.disabled
			or warehouse.is_group
			or warehouse.warehouse_type == "Transit"
		):
			frappe.throw(_("Choose active, same-company, non-transit stock warehouses."))
		if (
			name == destination
			and warehouse.get("is_rejected_warehouse")
			and "nl_vms" in frappe.get_installed_apps()
		):
			frappe.throw(_("Use a holding/quarantine warehouse, not an automatic write-off warehouse."))


def validate_depot_collection_case(request, proof):
	if proof.docstatus != 1 or proof.collection_party_type != "Depot" or request.company != proof.company:
		frappe.throw(_("Depot collection case must match its submitted collection evidence."))
	if request.customer or request.delivery_note or request.sales_invoice or request.source_stock_entry:
		frappe.throw(_("Depot collections use warehouse transfers, not customer return documents."))
	if request.source_warehouse != proof.collection_warehouse:
		frappe.throw(_("The depot case source warehouse must match the referenced stock receipt."))
	_validate_warehouses(request.company, request.source_warehouse, request.return_warehouse)
	if frappe.db.exists(
		"Return Request",
		{"proof_of_delivery": proof.name, "docstatus": ["!=", 2], "name": ["!=", request.name or ""]},
	):
		frappe.throw(_("This collection already has an active Return Request."))
	sources = {row.name: row for row in proof.items}
	seen = set()
	for row in request.items:
		source = sources.get(row.source_pod_item)
		if source:
			from vuna_transport.services.execution.return_uom_service import configure_return_uom

			configure_return_uom(row, source)
		if (
			not source
			or source.name in seen
			or abs(
				flt(row.requested_return_qty) * row.conversion_factor
				- flt(source.delivered_qty) * flt(source.conversion_factor)
			)
			> 1e-6
		):
			frappe.throw(_("Preserve each collected row and its original quantity in the Return Request."))
		seen.add(source.name)
		for field in (
			"item_code",
			"item_name",
			"batch_no",
			"serial_no",
		):
			row.set(field, source.get(field))
		row.custody_warehouse = request.source_warehouse
		# No original outbound delivery is being asserted for an unplanned collection.
		row.delivered_qty = 0
	if seen != set(sources):
		frappe.throw(_("Include every collected item in the Return Request."))
	from vuna_transport.services.execution.depot_return_service import depot_return_state

	state = depot_return_state(request)
	for field in ("status", "expected_return_qty", "returned_qty", "return_difference_qty"):
		request.set(field, state[field])
	for row in request.items:
		row.received_qty = state["returned_by_item"].get(row.name, 0)
		row.remaining_qty = max(flt(row.requested_return_qty) - row.received_qty, 0)


@frappe.whitelist()
def make_collection_stock_entry(source_name: str, target_doc: dict | str | None = None):
	"""Warehouse staff confirm physical arrival by submitting this standard draft."""
	from vuna_transport.services.shared.security_service import require_doctype_permission

	proof = frappe.get_doc("Proof of Delivery", source_name)
	_require_dispatch_review(proof)
	require_doctype_permission("Stock Entry", "create")
	if proof.collection_review_status != "Reviewed" or proof.collection_party_type != "Depot":
		frappe.throw(_("Review the depot collection and its Return Request first."))
	request = frappe.get_doc("Return Request", proof.return_request)
	require_document_permission(request)
	if request.docstatus != 1 or request.return_context != "Depot":
		frappe.throw(_("Submit the depot Return Request before receiving stock."))
	if existing := frappe.db.get_value(
		"Stock Entry", {"vuna_unplanned_collection": proof.name, "docstatus": ["!=", 2]}, "name"
	):
		require_document_permission(frappe.get_doc("Stock Entry", existing))
		return frappe.get_doc("Stock Entry", existing)
	entry = frappe.new_doc("Stock Entry")
	entry.update(
		{
			"stock_entry_type": "Material Transfer",
			"purpose": "Material Transfer",
			"company": proof.company,
			"from_warehouse": request.source_warehouse,
			"to_warehouse": request.return_warehouse,
			"vuna_unplanned_collection": proof.name,
			"vuna_return_request": request.name,
		}
	)
	for row, source in _collection_receipt_rows(proof, request):
		entry.append(
			"items",
			{
				"item_code": row.item_code,
				"qty": row.delivered_qty,
				"uom": row.uom,
				"stock_uom": row.stock_uom,
				"conversion_factor": row.conversion_factor,
				"s_warehouse": source.custody_warehouse or request.source_warehouse,
				"t_warehouse": request.return_warehouse,
				"batch_no": row.batch_no,
				"serial_no": row.serial_no,
				"use_serial_batch_fields": 1,
				"vuna_return_request_item": source.name,
				"vuna_transport_proof_item": row.name,
			},
		)
	return entry


def _collection_receipt_rows(proof, request):
	result = []
	for row in proof.items:
		matching = [item for item in request.items if item.source_pod_item == row.name]
		if not matching:
			matching = [
				item
				for item in request.items
				if item.item_code == row.item_code and (item.batch_no or "") == (row.batch_no or "")
			]
		if len(matching) != 1:
			frappe.throw(
				_("Resolve ambiguous item/batch rows in the Return Request before reviewing this collection.")
			)
		source = matching[0]
		if (
			proof.get("collection_warehouse")
			and (source.custody_warehouse or request.source_warehouse) != proof.collection_warehouse
		):
			frappe.throw(_("The Return Request pickup warehouse must match the referenced stock receipt."))
		_validate_warehouses(
			proof.company, source.custody_warehouse or request.source_warehouse, request.return_warehouse
		)
		result.append((row, source))
	return result


def unplanned_collected_quantities(request):
	quantities = defaultdict(float)
	for name in frappe.get_all(
		"Proof of Delivery",
		filters={
			"proof_type": PROOF_TYPE,
			"return_request": request.name,
			"docstatus": 1,
			"collection_review_status": "Reviewed",
		},
		pluck="name",
	):
		proof = frappe.get_doc("Proof of Delivery", name)
		for row, source in _collection_receipt_rows(proof, request):
			quantities[source.name] += flt(row.stock_qty)
	return quantities


def validate_collection_stock(entry, method=None):
	old = entry.get_doc_before_save()
	if old and old.get("vuna_unplanned_collection") != entry.get("vuna_unplanned_collection"):
		frappe.throw(_("The collection stock link cannot be changed after saving."))
	if not entry.get("vuna_unplanned_collection"):
		return
	frappe.db.get_value("Proof of Delivery", entry.vuna_unplanned_collection, "name", for_update=True)
	proof = frappe.get_doc("Proof of Delivery", entry.vuna_unplanned_collection)
	request = frappe.get_doc("Return Request", entry.vuna_return_request)
	if (
		proof.docstatus != 1
		or proof.proof_type != PROOF_TYPE
		or proof.collection_review_status != "Reviewed"
		or proof.return_request != request.name
		or request.docstatus != 1
		or request.return_context != "Depot"
		or entry.company != proof.company
		or entry.company != request.company
		or entry.purpose != "Material Transfer"
		or entry.add_to_transit
		or entry.outgoing_stock_entry
	):
		frappe.throw(
			_("Stock receipt must match the reviewed depot collection and submitted Return Request.")
		)
	if frappe.db.exists(
		"Stock Entry",
		{"vuna_unplanned_collection": proof.name, "docstatus": ["!=", 2], "name": ["!=", entry.name or ""]},
	):
		frappe.throw(_("This collection already has an active stock receipt."))
	sources = {row.name: (row, source) for row, source in _collection_receipt_rows(proof, request)}
	seen = set()
	for row in entry.items:
		pair = sources.get(row.vuna_transport_proof_item)
		source, request_item = pair if pair else (None, None)
		if (
			not source
			or source.name in seen
			or row.item_code != source.item_code
			or row.vuna_return_request_item != request_item.name
			or flt(row.qty) != flt(source.delivered_qty)
			or row.uom != source.uom
			or flt(row.conversion_factor) != flt(source.conversion_factor)
			or row.s_warehouse != (request_item.custody_warehouse or request.source_warehouse)
			or row.t_warehouse != request.return_warehouse
		):
			frappe.throw(
				_("Stock rows must preserve collected items, quantities, UOM and approved warehouses.")
			)
		seen.add(source.name)
		if row.serial_and_batch_bundle:
			bundle = frappe.get_doc("Serial and Batch Bundle", row.serial_and_batch_bundle)
			batches = {detail.batch_no for detail in bundle.entries if detail.batch_no}
			serials = {detail.serial_no for detail in bundle.entries if detail.serial_no}
			if batches != ({source.batch_no} if source.batch_no else set()) or serials != set(
				(source.serial_no or "").splitlines()
			):
				frappe.throw(_("Stock batch/serial identity must match the collected goods."))
		elif (row.batch_no or "") != (source.batch_no or "") or set(
			(row.serial_no or "").splitlines()
		) != set((source.serial_no or "").splitlines()):
			frappe.throw(_("Stock batch/serial identity must match the collected goods."))
	if seen != set(sources):
		frappe.throw(
			_("Receive every collected row together; partial unplanned receipts are not supported yet.")
		)


def sync_collection_stock(entry, method=None):
	if entry.get("vuna_unplanned_collection") and entry.get("vuna_return_request"):
		from vuna_transport.services.execution.return_service import recalculate_return_request

		recalculate_return_request(entry.vuna_return_request)


def unlink_collection_cases(request):
	for name in frappe.get_all(
		"Proof of Delivery",
		filters={"return_request": request.name, "proof_type": PROOF_TYPE, "docstatus": 1},
		pluck="name",
	):
		proof = frappe.get_doc("Proof of Delivery", name)
		proof.db_set(
			{
				"return_request": None,
				"collection_review_status": "Pending Review",
				"collection_reviewed_by": None,
				"collection_reviewed_at": None,
				"collection_review_notes": None,
			}
		)
		proof.add_comment(
			"Info",
			_(
				"Return Request {0} was cancelled/deleted. Collection evidence reopened for dispatch review."
			).format(request.name),
		)
