"""Operational transfer evidence and reviewed ERPNext end-transit drafts."""

import hashlib
import math

import frappe
from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
from frappe import _
from frappe.utils import flt, now_datetime

from vuna_transport.services.execution.depot_custody_service import receipt_groups, validate_depot_custody
from vuna_transport.services.execution.transfer_execution_service import (
	_lock_task,
	get_dispatch_entry,
)
from vuna_transport.services.shared.security_service import (
	require_doctype_permission,
	require_document_permission,
)


def proof_context(task):
	trip = frappe.get_doc("Delivery Trip", task.delivery_trip)
	if (
		task.task_type != "Transfer Receipt"
		or task.status == "Cancelled"
		or trip.docstatus != 1
		or not trip.vuna_task_based
	):
		frappe.throw(
			_(
				"POD is recorded at the receiving stop. Loading is already confirmed by the allocation's posted stock entry."
			)
		)
	name = get_dispatch_entry(task.name)
	stock = frappe.get_doc("Stock Entry", name) if name else None
	if (
		not stock
		and frappe.db.get_value("Transport Demand", task.transport_demand, "source_document_type")
		== "Return Request"
	):
		frappe.throw(_("Record the return pickup POD before confirming receipt at the destination."))
	if not stock or stock.docstatus != 1 or not stock.add_to_transit or stock.company != task.company:
		frappe.throw(_("Submit the linked Add-to-Transit Stock Entry before recording transfer proof."))
	if stock.get("vuna_vehicle_allocation"):
		allowed = {row.load_item for row in task.items}
		stock.set("items", [row for row in stock.items if row.vuna_transport_load_item in allowed])
		if not stock.items:
			frappe.throw(_("The dispatch Stock Entry does not contain this stop's allocated cargo."))
	return trip, stock


def stock_identities(row):
	"""Prefer the posted bundle; legacy serial/batch fields are fallback only."""
	groups = {}
	if row.serial_and_batch_bundle:
		bundle = frappe.get_doc("Serial and Batch Bundle", row.serial_and_batch_bundle)
		for identity in bundle.entries:
			group = groups.setdefault(identity.batch_no or "", {"stock_qty": 0, "serials": []})
			group["stock_qty"] += abs(flt(identity.qty))
			if identity.serial_no:
				group["serials"].append(identity.serial_no)
	else:
		groups[row.batch_no or ""] = {
			"stock_qty": flt(row.transfer_qty),
			"serials": get_serial_nos(row.serial_no or ""),
		}
	return groups


def planned_proof_items(stock):
	items = []
	for row in stock.items:
		factor = flt(row.conversion_factor)
		if not math.isfinite(factor) or factor <= 0:
			frappe.throw(_("Posted dispatch has an invalid UOM conversion factor."))
		has_batch, has_serial = frappe.get_cached_value(
			"Item", row.item_code, ["has_batch_no", "has_serial_no"]
		)
		if not row.serial_and_batch_bundle and (
			(has_batch and not row.batch_no) or (has_serial and not row.serial_no)
		):
			frappe.throw(
				_("Posted dispatch is missing batch/serial identity. Correct its stock document first.")
			)
		for batch, identity in stock_identities(row).items():
			items.append(
				{
					"source_row": row.name,
					"item_code": row.item_code,
					"uom": row.uom,
					"stock_uom": row.stock_uom,
					"conversion_factor": factor,
					"ordered_qty": identity["stock_qty"] / factor,
					"delivered_qty": identity["stock_qty"] / factor,
					"stock_qty": identity["stock_qty"],
					"batch_no": batch or None,
					"serial_no": "\n".join(identity["serials"]),
					"source_bundle": row.serial_and_batch_bundle,
				}
			)
	return items


@frappe.whitelist()
def make_transfer_proof(source_name: str):
	require_doctype_permission("Proof of Delivery", "create")
	task = _lock_task(source_name)
	require_document_permission(task)
	return prepare_proof(task)


def prepare_proof(task):
	if task.task_type == "Return Pickup":
		from vuna_transport.services.execution.return_pickup_service import prepare_pickup_proof

		return prepare_pickup_proof(task)
	trip, stock = proof_context(task)
	if existing := frappe.db.get_value(
		"Proof of Delivery", {"transport_trip_task": task.name, "docstatus": ["!=", 2]}, "name"
	):
		return frappe.get_doc("Proof of Delivery", existing)
	if trip.vuna_actual_completion:
		frappe.throw(_("The trip is already completed."))
	proof = frappe.new_doc("Proof of Delivery")
	proof.transport_trip_task = task.name
	proof.company, proof.delivery_trip, proof.delivery_stop = (
		task.company,
		task.delivery_trip,
		task.delivery_stop,
	)
	proof.proof_type, proof.dispatch_stock_entry = task.task_type, stock.name
	proof.delivery_load, proof.delivery_address = task.transport_load, task.address
	proof.delivery_datetime = now_datetime()
	proof.direction = "Dispatch" if task.task_type == "Transfer Dispatch" else "Delivery"
	proof.received_by = ""
	proof.set("items", planned_proof_items(stock))
	proof.outcome = "Completed"
	return proof


def validate_proof_items(rows, stock, dispatch=False):
	"""Every dispatched batch group is accounted for, even when received qty is zero."""
	expected = {(r["source_row"], r["batch_no"] or ""): r for r in planned_proof_items(stock)}
	seen, serials = set(), set()
	actual, planned = 0, 0
	precision = frappe.get_precision("Stock Entry Detail", "transfer_qty")
	for row in rows:
		key = (row.source_row, row.batch_no or "")
		source = expected.get(key)
		if not source or key in seen:
			frappe.throw(_("Proof rows must match the dispatched item and batch groups exactly."))
		seen.add(key)
		qty = flt(row.delivered_qty)
		rejected_qty = flt(getattr(row, "rejected_qty", 0))
		if not math.isfinite(qty) or qty < 0 or not math.isfinite(rejected_qty) or rejected_qty < 0:
			frappe.throw(_("Actual quantities must be finite and non-negative."))
		stock_qty = flt(qty * source["conversion_factor"], precision)
		rejected_stock = flt(rejected_qty * source["conversion_factor"], precision)
		for uom, value in ((source["uom"], qty), (source["stock_uom"], stock_qty)):
			if (
				frappe.get_cached_value("UOM", uom, "must_be_whole_number")
				and abs(value - round(value)) > 1e-9
			):
				frappe.throw(
					_(
						"UOM {0} requires whole quantities; resolve packaging splits before recording proof."
					).format(uom)
				)
		limit = flt(source["stock_qty"], precision)
		if flt(stock_qty + rejected_stock - limit, precision) > 0 or (
			dispatch and (rejected_stock or flt(stock_qty - limit, precision))
		):
			frappe.throw(_("Receipt cannot exceed dispatch; dispatch proof must match posted stock."))
		chosen = get_serial_nos(row.serial_no or "")
		allowed = get_serial_nos(source["serial_no"])
		if (
			len(chosen) != len(set(chosen))
			or serials.intersection(chosen)
			or not set(chosen).issubset(allowed)
			or (allowed and len(chosen) != stock_qty)
		):
			frappe.throw(
				_("Select exactly the dispatched serial numbers matching the actual stock quantity.")
			)
		serials.update(chosen)
		rejected_serials = get_serial_nos(getattr(row, "rejected_serial_no", None) or "")
		if (
			len(rejected_serials) != len(set(rejected_serials))
			or serials.intersection(rejected_serials)
			or not set(rejected_serials).issubset(allowed)
			or (allowed and len(rejected_serials) != rejected_stock)
		):
			frappe.throw(
				_("Rejected serial numbers must be distinct dispatched serials matching rejected quantity.")
			)
		serials.update(rejected_serials)
		if rejected_qty and not (
			getattr(row, "rejection_reason", None) or getattr(row, "rejection_reason_remarks", None)
		):
			frappe.throw(_("Explain the rejection for item {0}.").format(source["item_code"]))
		for field in ("item_code", "uom", "stock_uom", "conversion_factor", "ordered_qty", "source_bundle"):
			setattr(row, field, source[field])
		row.stock_qty = stock_qty
		row.rejected_stock_qty = rejected_stock
		row.missing_qty = max(flt(source["ordered_qty"]) - qty - rejected_qty, 0)
		actual += stock_qty
		planned += limit
	if seen != set(expected):
		frappe.throw(_("Keep every dispatched item/batch row; enter zero for goods not received."))
	return (
		"Completed"
		if flt(actual - planned, precision) == 0
		else "Failed"
		if actual == 0
		else "Partially Completed"
	)


def validate_proof(doc):
	previous = doc.get_doc_before_save()
	if previous and previous.transport_trip_task != doc.transport_trip_task:
		frappe.throw(_("The proof task cannot be changed on a saved document."))
	task = _lock_task(doc.transport_trip_task)
	if not doc.flags.ignore_permissions:
		require_document_permission(task)
	trip, stock = proof_context(task)
	duplicate = frappe.db.get_value(
		"Proof of Delivery",
		{"transport_trip_task": task.name, "docstatus": ["!=", 2], "name": ["!=", doc.name or ""]},
		"name",
	)
	if duplicate:
		frappe.throw(_("This task already has active proof: {0}.").format(duplicate))
	if trip.vuna_actual_completion:
		frappe.throw(_("The trip is already completed."))
	if not trip.vuna_actual_start:
		frappe.throw(_("Start the trip before recording delivery at the receiving stop."))
	doc.company, doc.delivery_trip, doc.delivery_stop = task.company, task.delivery_trip, task.delivery_stop
	doc.proof_type, doc.dispatch_stock_entry = task.task_type, stock.name
	doc.delivery_load, doc.delivery_address = task.transport_load, task.address
	doc.direction = "Dispatch" if task.task_type == "Transfer Dispatch" else "Delivery"
	for row in doc.items:
		if flt(row.rejected_qty) and not (row.rejection_reason or row.rejection_reason_remarks):
			row.rejection_reason_remarks = doc.remarks
	doc.outcome = validate_proof_items(doc.items, stock, task.task_type == "Transfer Dispatch")
	doc.total_accepted_qty = sum(flt(row.delivered_qty) for row in doc.items)
	doc.total_rejected_qty = sum(flt(row.rejected_qty) for row in doc.items)
	demand = frappe.get_doc("Transport Demand", task.transport_demand)
	validate_depot_custody(doc, stock, demand.destination_warehouse)
	doc.status = (
		"Accepted"
		if doc.outcome == "Completed"
		else "Partially Delivered"
		if doc.total_accepted_qty
		else "Rejected"
		if doc.total_rejected_qty
		else "Failed"
	)
	doc.delivery_datetime = doc.delivery_datetime or now_datetime()
	if doc.docstatus == 1:
		if not (doc.received_by or "").strip():
			frappe.throw(_("Enter the name of the person confirming this handover."))
		# Rejected quantities already require a row-level reason. Only an
		# unaccounted balance needs an additional explanation on the proof.
		precision = frappe.get_precision("Stock Entry Detail", "transfer_qty")
		if (
			any(flt(flt(row.missing_qty) * flt(row.conversion_factor), precision) > 0 for row in doc.items)
			and not (doc.remarks or "").strip()
		):
			frappe.throw(_("Explain the missing or unaccounted quantity in Remarks."))
		doc.recorded_by = frappe.session.user
		doc.delivery_datetime = now_datetime()


def derive_transfer_status(task):
	if task.task_type == "Transfer Dispatch":
		# Historical loading records are satisfied by stock posting, not by
		# asking the driver to create another POD.
		entry = get_dispatch_entry(task.name)
		return (
			"Completed" if entry and frappe.db.get_value("Stock Entry", entry, "docstatus") == 1 else "Ready"
		)
	proof = frappe.db.get_value(
		"Proof of Delivery",
		{"transport_trip_task": task.name, "docstatus": 1},
		["name", "outcome"],
		as_dict=True,
	)
	if not proof:
		if (
			task.task_type == "Transfer Receipt"
			and frappe.db.get_value("Transport Demand", task.transport_demand, "source_document_type")
			== "Return Request"
		):
			from vuna_transport.services.execution.return_pickup_service import failed_pickup

			if failed_pickup(task):
				return "Failed"
		return "Ready"
	if not frappe.db.exists("Stock Entry", {"vuna_transport_proof": proof.name, "docstatus": 1}):
		depot_receipt = proof.outcome == "Failed" and frappe.db.exists(
			"Proof of Delivery Item",
			{
				"parent": proof.name,
				"parenttype": "Proof of Delivery",
				"depot_received_rejected_qty": [">", 0],
			},
		)
		return "Failed" if proof.outcome == "Failed" and not depot_receipt else "Awaiting Stock Posting"
	return "Completed" if proof.outcome == "Completed" else "Completed with Variance"


def sync_proof(doc, method=None):
	from vuna_transport.services.execution.trip_task_service import sync_task_execution

	sync_task_execution(doc)


def guard_proof_cancellation(doc):
	task = _lock_task(doc.transport_trip_task)
	trip = frappe.get_doc("Delivery Trip", task.delivery_trip)
	if task.task_type == "Transfer Dispatch" and trip.vuna_actual_start:
		frappe.throw(_("Dispatch proof cannot be cancelled after departure."))
	if frappe.db.exists("Stock Entry", {"vuna_transport_proof": doc.name, "docstatus": ["!=", 2]}):
		frappe.throw(_("Cancel or delete the linked End Transit Stock Entry before cancelling its proof."))


@frappe.whitelist()
def make_receipt_stock_entry(source_name: str):
	"""ERPNext mapping pattern, with proof quantities applied before bundle creation."""
	require_doctype_permission("Stock Entry", "create")
	proof = frappe.get_doc("Proof of Delivery", source_name)
	require_document_permission(proof)
	return _make_receipt_stock_entry(proof)


def _make_receipt_stock_entry(proof, check_permissions=True, allow_draft_proof=False):
	task = _lock_task(proof.transport_trip_task)
	_trip, stock = proof_context(task)
	if check_permissions:
		require_document_permission(stock)
	if (
		proof.docstatus not in ((0, 1) if allow_draft_proof else (1,))
		or proof.proof_type != "Transfer Receipt"
		or not receipt_groups(proof)
	):
		frappe.throw(_("Submit a receipt proof with positive received quantities first."))
	if existing := frappe.db.get_value(
		"Stock Entry", {"vuna_transport_proof": proof.name, "docstatus": ["!=", 2]}, "name"
	):
		entry = frappe.get_doc("Stock Entry", existing)
		if check_permissions:
			require_document_permission(entry)
		return entry
	from frappe.model.mapper import get_mapped_doc

	entry = get_mapped_doc(
		"Stock Entry",
		stock.name,
		{
			"Stock Entry": {
				"doctype": "Stock Entry",
				"field_map": {"name": "outgoing_stock_entry"},
				"validation": {"docstatus": ["=", 1], "add_to_transit": ["=", 1]},
			},
			"Stock Entry Detail": {
				"doctype": "Stock Entry Detail",
				"field_map": {"name": "ste_detail", "parent": "against_stock_entry"},
			},
		},
		ignore_permissions=not check_permissions,
	)
	templates = {row.ste_detail: row for row in entry.items}
	sources = {row.name: row for row in stock.items}
	demand = frappe.get_doc("Transport Demand", task.transport_demand)
	entry.set("items", [])
	entry.vuna_transport_task = None
	entry.vuna_return_pickup_proof = None
	entry.vuna_vehicle_allocation = entry.vuna_transport_load = None
	entry.vuna_transport_proof = proof.name
	entry.stock_entry_type = entry.purpose = "Material Transfer"
	entry.add_to_transit = 0
	entry.from_warehouse, entry.to_warehouse = stock.to_warehouse, demand.destination_warehouse
	for group in receipt_groups(proof):
		result = group.result
		row = frappe.copy_doc(templates[result.source_row])
		row.ste_detail, row.against_stock_entry = result.source_row, stock.name
		row.qty, row.uom, row.conversion_factor = group.qty, result.uom, result.conversion_factor
		row.stock_uom, row.transfer_qty = (
			result.stock_uom,
			flt(group.qty * flt(result.conversion_factor), row.precision("transfer_qty")),
		)
		row.s_warehouse = stock.to_warehouse
		row.t_warehouse = (
			demand.destination_warehouse if group.kind == "Accepted" else proof.rejected_goods_warehouse
		)
		row.material_request = row.material_request_item = None
		row.vuna_transport_task_item = None
		row.vuna_transport_proof_item = result.name
		row.vuna_transport_proof_outcome = group.kind
		row.serial_and_batch_bundle = row.serial_no = row.batch_no = None
		row.quality_inspection = None
		row.use_serial_batch_fields = 0
		entry.append("items", row)
		if sources[result.source_row].serial_and_batch_bundle or result.batch_no or group.serial_no:
			if not check_permissions:
				# Use a standard bundle with exact, already-validated identities.
				# Do not grant the driver general bundle creation permissions.
				serials = get_serial_nos(group.serial_no)
				bundle = frappe.get_doc(
					{
						"doctype": "Serial and Batch Bundle",
						"item_code": row.item_code,
						"company": entry.company,
						"warehouse": row.s_warehouse,
						"type_of_transaction": "Outward",
						"voucher_type": "Stock Entry",
						"entries": [
							{"serial_no": serial, "batch_no": result.batch_no, "qty": -1}
							for serial in serials
						]
						if serials
						else [{"batch_no": result.batch_no, "qty": -flt(row.transfer_qty)}],
					}
				).insert(ignore_permissions=True)
				row.serial_and_batch_bundle = bundle.name
				continue
			from erpnext.stock.serial_batch_bundle import SerialBatchCreation

			bundle = SerialBatchCreation(
				frappe._dict(
					item_code=row.item_code,
					company=entry.company,
					warehouse=row.s_warehouse,
					type_of_transaction="Outward",
					voucher_type="Stock Entry",
					qty=-flt(row.transfer_qty),
					do_not_submit=True,
				)
			).make_serial_and_batch_bundle(
				serial_nos=get_serial_nos(group.serial_no),
				batch_nos={result.batch_no: row.transfer_qty} if result.batch_no else None,
			)
			row.serial_and_batch_bundle = bundle.name
	targets = {row.t_warehouse for row in entry.items}
	entry.to_warehouse = next(iter(targets)) if len(targets) == 1 else None
	entry.set_missing_values()
	if stock.get("vuna_vehicle_allocation"):
		from vuna_transport.services.execution.allocation_transfer_service import (
			set_allocation_stock_defaults,
		)

		set_allocation_stock_defaults(
			entry, frappe.get_doc("Vehicle Allocation", stock.vuna_vehicle_allocation)
		)
	entry.set_transfer_qty()
	entry.calculate_rate_and_amount(raise_error_if_no_rate=False)
	return entry


def submit_receipt_stock_from_pod(proof):
	"""Post only the receipt derived from validated POD, in its submission transaction.

	Drivers receive no general Stock Entry permissions. This non-whitelisted
	operation uses trusted proof quantities/destination; ERPNext validations,
	quality requirements, stock valuation and ledger posting remain enabled.
	"""
	if (
		proof.docstatus != 1
		or proof.proof_type != "Transfer Receipt"
		or not proof.transport_trip_task
		or not receipt_groups(proof)
	):
		return
	entry = _make_receipt_stock_entry(proof, check_permissions=False)
	if entry.docstatus == 1:
		return entry.name
	entry.flags.ignore_permissions = True
	if entry.is_new():
		entry.insert(ignore_permissions=True)
	entry.submit()
	return entry.name


def validate_receipt_stock_entry(doc, method=None):
	previous = doc.get_doc_before_save()
	if previous and previous.get("vuna_transport_proof") != doc.get("vuna_transport_proof"):
		frappe.throw(_("The transfer proof link cannot be changed on a saved Stock Entry."))
	if not doc.get("vuna_transport_proof"):
		return
	proof = frappe.get_doc("Proof of Delivery", doc.vuna_transport_proof)
	_lock_task(proof.transport_trip_task)
	if not doc.flags.ignore_permissions:
		require_document_permission(proof)
	if (
		(proof.docstatus != 1 and not (proof.docstatus == 0 and doc.docstatus == 0))
		or proof.proof_type != "Transfer Receipt"
		or not receipt_groups(proof)
	):
		frappe.throw(_("End Transit requires a submitted receipt proof."))
	task = frappe.get_doc("Transport Trip Task", proof.transport_trip_task)
	_trip, stock = proof_context(task)
	demand = frappe.get_doc("Transport Demand", task.transport_demand)
	other = frappe.db.get_value(
		"Stock Entry",
		{"vuna_transport_proof": proof.name, "docstatus": ["!=", 2], "name": ["!=", doc.name or ""]},
		"name",
	)
	if (
		other
		or doc.vuna_transport_task
		or doc.add_to_transit
		or doc.purpose != "Material Transfer"
		or (doc.outgoing_stock_entry != stock.name or doc.company != proof.company)
	):
		frappe.throw(_("End Transit must retain its unique proof and outgoing Stock Entry."))
	expected = {(group.result.name, group.kind): group for group in receipt_groups(proof)}
	seen = set()
	for row in doc.items:
		key = (row.vuna_transport_proof_item, row.get("vuna_transport_proof_outcome") or "Accepted")
		group = expected.get(key)
		result = group.result if group else None
		if (
			not result
			or key in seen
			or (
				row.ste_detail != result.source_row
				or row.against_stock_entry != stock.name
				or row.item_code != result.item_code
				or row.uom != result.uom
				or row.stock_uom != result.stock_uom
				or flt(row.conversion_factor) != flt(result.conversion_factor)
				or row.s_warehouse != stock.to_warehouse
				or row.t_warehouse
				!= (
					demand.destination_warehouse
					if group.kind == "Accepted"
					else proof.rejected_goods_warehouse
				)
				or flt(
					flt(row.transfer_qty) - group.qty * flt(result.conversion_factor),
					row.precision("transfer_qty"),
				)
				or flt(flt(row.qty) - group.qty, row.precision("qty"))
			)
		):
			frappe.throw(_("End Transit rows must match actual receipt proof, UOM and destination."))
		identities = stock_identities(row)
		serials = [serial for identity in identities.values() for serial in identity["serials"]]
		if set(identities) != {result.batch_no or ""} or set(serials) != set(get_serial_nos(group.serial_no)):
			frappe.throw(_("End Transit batch/serial identity must match the receipt proof."))
		if flt(
			identities[result.batch_no or ""]["stock_qty"] - group.qty * flt(result.conversion_factor),
			row.precision("transfer_qty"),
		):
			frappe.throw(_("End Transit bundle quantity must match the receipt proof."))
		seen.add(key)
	if seen != set(expected):
		frappe.throw(_("End Transit must include every positive receipt proof row."))


def sync_receipt_stock(doc, method=None):
	if doc.get("vuna_transport_proof"):
		proof = frappe.get_doc("Proof of Delivery", doc.vuna_transport_proof)
		sync_proof(proof)
		task = frappe.get_doc("Transport Trip Task", proof.transport_trip_task)
		demand = frappe.get_doc("Transport Demand", task.transport_demand)
		if demand.source_document_type == "Return Request":
			from vuna_transport.services.execution.return_service import recalculate_return_request

			recalculate_return_request(demand.source_document)


def submit_mobile_transfer_proof(installation_id, task_name, payload, device_identifier=None):
	from vuna_transport.services.mobile.mobile_device_service import (
		_get_assigned_trip,
		_require_mobile_driver,
	)
	from vuna_transport.services.mobile.mobile_trip_execution_service import _validate_request_id

	driver = _require_mobile_driver(installation_id, device_identifier)
	task = _lock_task(task_name)
	_get_assigned_trip(task.delivery_trip, driver.name)
	data = frappe.parse_json(payload) if isinstance(payload, str) else payload
	if not isinstance(data, dict) or len(frappe.as_json(data)) > 250_000:
		frappe.throw(_("Invalid transfer proof payload."))
	request_id = _validate_request_id(data.get("request_id"))
	key = hashlib.sha256(f"{driver.name}:{request_id}".encode()).hexdigest()
	digest = hashlib.sha256(frappe.as_json(data).encode()).hexdigest()
	if name := frappe.db.get_value("Proof of Delivery", {"mobile_request_id": key}, "name"):
		existing = frappe.get_doc("Proof of Delivery", name)
		if (
			existing.transport_trip_task != task.name
			or existing.payload_hash != digest
			or existing.docstatus != 1
		):
			frappe.throw(_("The request ID has already been used for another proof payload."))
		return {"name": existing.name, "outcome": existing.outcome}
	proof = prepare_proof(task)
	if proof.docstatus != 0:
		frappe.throw(_("This task already has submitted POD. Review it instead of submitting again."))
	rows = data.get("items")
	if not isinstance(rows, list) or not rows or len(rows) > 5000:
		frappe.throw(_("Provide between 1 and 5,000 proof item groups."))
	proof.set(
		"items",
		[
			{
				**{
					field: row.get(field)
					for field in (
						"source_row",
						"batch_no",
						"serial_no",
						"rejected_qty",
						"rejected_serial_no",
						"depot_received_rejected_qty",
						"rejected_goods_handling",
						"depot_received_rejected_serial_no",
						"rejection_reason",
						"rejection_reason_remarks",
					)
				},
				"delivered_qty": row.get("delivered_qty", row.get("qty")),
			}
			for row in rows
			if isinstance(row, dict)
		],
	)
	proof.received_by = str(data.get("received_by") or data.get("confirmed_by") or "")[:140]
	proof.receiver_phone = str(data.get("receiver_phone") or "")[:140]
	proof.remarks = str(data.get("remarks") or "")[:4000]
	proof.mobile_request_id, proof.payload_hash = key, digest
	proof.flags.ignore_permissions = True
	if proof.is_new():
		proof.insert(ignore_permissions=True)
	else:
		proof.save(ignore_permissions=True)
	proof.submit()
	return {"name": proof.name, "outcome": proof.outcome}
