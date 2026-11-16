"""Reserve only POD-confirmed rejected cargo still on the same vehicle."""

import frappe
from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
from frappe import _
from frappe.utils import flt

from vuna_transport.services.shared.security_service import (
	require_doctype_permission,
	require_document_permission,
)


def root_diversion(doc):
	seen = set()
	while doc.get("previous_diversion"):
		if doc.name in seen or len(seen) >= 20:
			frappe.throw(_("The diversion follow-up chain is invalid or too long. Review its history."))
		seen.add(doc.name)
		parent = frappe.get_doc("Shipment Diversion", doc.previous_diversion)
		require_document_permission(parent, "read")
		if (
			parent.docstatus != 1
			or parent.destination_type != "Depot"
			or any(
				parent.get(field) != doc.get(field)
				for field in (
					"return_request",
					"company",
					"delivery_note",
					"proof_of_delivery",
					"delivery_trip",
					"vehicle",
					"vehicle_warehouse",
					"driver",
				)
			)
		):
			frappe.throw(
				_("Follow-up diversions must retain their approved source cargo and original vehicle.")
			)
		doc = parent
	return doc


def followup_candidates(doc, context):
	parent = frappe.get_doc("Shipment Diversion", doc.previous_diversion, for_update=True)
	root_diversion(doc)
	proof = frappe.get_doc("Proof of Delivery", doc.previous_proof_of_delivery, for_update=True)
	if (
		proof.docstatus != 1
		or proof.transport_trip_task != parent.get("execution_task")
		or proof.proof_type != "Transfer Receipt"
		or proof.company != parent.company
	):
		frappe.throw(_("Choose the submitted depot rejection proof for the preceding diversion."))
	rows = {row.source_row: row for row in proof.items}
	base = {row["return_request_item"]: row for row in context["items"]}
	candidates = {}
	for row in parent.items:
		actual = rows.get(row.name)
		if not actual:
			continue
		quantity = max(flt(actual.rejected_qty) - flt(actual.depot_received_rejected_qty), 0)
		if quantity <= 0:
			continue
		serials = set(get_serial_nos(actual.rejected_serial_no or "")) - set(
			get_serial_nos(actual.depot_received_rejected_serial_no or "")
		)
		item = dict(base[row.return_request_item])
		item.update(
			stock_qty=quantity * row.conversion_factor,
			source_rejected_stock_qty=quantity * row.conversion_factor,
			rejected_serial_no="\n".join(sorted(serials)),
			source_diversion_item=row.name,
			origin_diversion_item=row.get("origin_diversion_item") or row.name,
		)
		candidates[row.return_request_item] = item
	reservations = frappe.db.sql(
		"""select i.return_request_item, i.proof_of_delivery_item, i.stock_qty, i.serial_no
		from `tabShipment Diversion Item` i join `tabShipment Diversion` d on d.name=i.parent
		where d.previous_diversion=%s and d.docstatus in (0,1) and d.name!=%s
		and i.parenttype='Shipment Diversion' for update""",
		(parent.name, doc.name or ""),
		as_dict=True,
	)
	for used in frappe.db.sql(
		"""select sitem.*, p.source_row from `tabStock Entry Detail` sitem
		join `tabStock Entry` s on s.name=sitem.parent
		join `tabReturn Request Item` r on r.name=sitem.vuna_return_request_item
		join `tabReturn Request` request on request.name=r.parent
		join `tabProof of Delivery Item` p on p.name=r.source_pod_item
		where request.proof_of_delivery=%s and request.docstatus!=2 and s.docstatus in (0,1)
		and s.add_to_transit=0 and r.custody_warehouse=%s for update""",
		(proof.name, parent.vehicle_warehouse),
		as_dict=True,
	):
		for item in candidates.values():
			if item["source_diversion_item"] == used.source_row:
				from vuna_transport.services.execution.shipment_diversion_documents import _identity

				serials = [
					serial
					for identity in _identity(used, used.transfer_qty).values()
					for serial in identity["serials"]
				]
				reservations.append(
					frappe._dict(
						return_request_item=item["return_request_item"],
						proof_of_delivery_item=item["proof_of_delivery_item"],
						stock_qty=used.transfer_qty,
						serial_no="\n".join(serials),
					)
				)
	return candidates, reservations


def validate_shared_receipt(doc, receipt):
	sources = {row.get("vuna_diversion_item"): row for row in receipt.items}
	for row in doc.items:
		source = sources.get(row.origin_diversion_item)
		if (
			not source
			or source.item_code != row.item_code
			or source.uom != row.uom
			or source.stock_uom != row.stock_uom
			or flt(source.conversion_factor) != row.conversion_factor
			or abs(flt(source.stock_qty)) + 1e-6 < row.stock_qty
		):
			frappe.throw(
				_("The follow-up must reuse its original posted vehicle Sales Return and stock identity.")
			)


def completed_followup_stock(parent_name, seen=None):
	"""Documented onward movement, distinct from goods received back into returns stock."""
	seen = set(seen or ())
	if parent_name in seen or len(seen) >= 20:
		frappe.throw(_("Review the cyclic or excessively long diversion chain."))
	seen.add(parent_name)
	totals = {}
	for name in frappe.get_all(
		"Shipment Diversion", filters={"previous_diversion": parent_name, "docstatus": 1}, pluck="name"
	):
		child = frappe.get_doc("Shipment Diversion", name)
		if not child.get("execution_task"):
			continue
		if child.destination_type == "Customer":
			if not child.get("onward_delivery_note") or not child.get("execution_stop"):
				continue
			proof = frappe.db.exists(
				"Proof of Delivery",
				{
					"delivery_trip": child.delivery_trip,
					"delivery_stop": child.execution_stop,
					"delivery_note": child.onward_delivery_note,
					"docstatus": 1,
				},
			)
			posted = frappe.db.get_value("Delivery Note", child.onward_delivery_note, "docstatus") == 1
			for row in child.items:
				if proof and posted:
					totals[row.source_diversion_item] = (
						totals.get(row.source_diversion_item, 0) + row.stock_qty
					)
		else:
			proof_name = frappe.db.get_value(
				"Proof of Delivery", {"transport_trip_task": child.execution_task, "docstatus": 1}, "name"
			)
			onward = completed_followup_stock(child.name, seen)
			unloaded = {}
			if (
				proof_name
				and child.get("stock_entry")
				and frappe.db.get_value("Stock Entry", child.stock_entry, "docstatus") == 1
			):
				from vuna_transport.services.execution.depot_custody_service import receipt_groups
				from vuna_transport.services.execution.shipment_diversion_exceptions import canonical_source

				proof = frappe.get_doc("Proof of Delivery", proof_name)
				for group in receipt_groups(proof):
					key = canonical_source(child, proof, group.result.source_row)
					unloaded[key] = unloaded.get(key, 0) + group.qty * group.result.conversion_factor
			for row in child.items:
				quantity = min(row.stock_qty, unloaded.get(row.name, 0) + onward.get(row.name, 0))
				totals[row.source_diversion_item] = totals.get(row.source_diversion_item, 0) + quantity
	return totals


def sync_ancestor_requests(doc):
	from vuna_transport.services.execution.return_service import recalculate_return_request

	recalculate_return_request(doc.return_request)
	seen = set()
	while doc.get("previous_diversion"):
		if doc.name in seen or len(seen) >= 20:
			break
		seen.add(doc.name)
		parent = doc.previous_diversion
		for name in frappe.get_all(
			"Return Request", filters={"shipment_diversion": parent, "docstatus": ["!=", 2]}, pluck="name"
		):
			recalculate_return_request(name)
		doc = frappe.get_doc("Shipment Diversion", parent)


def settled_stock(doc):
	"""Physical stock disposition including settled follow-ups, capped at the approved cargo."""
	onward = completed_followup_stock(doc.name)
	result = dict.fromkeys((row.name for row in doc.items), 0.0)
	if not doc.get("execution_task"):
		return result
	if doc.destination_type == "Customer":
		if (
			doc.get("onward_delivery_note")
			and frappe.db.get_value("Delivery Note", doc.onward_delivery_note, "docstatus") == 1
			and frappe.db.exists(
				"Proof of Delivery",
				{
					"delivery_trip": doc.delivery_trip,
					"delivery_stop": doc.execution_stop,
					"delivery_note": doc.onward_delivery_note,
					"docstatus": 1,
				},
			)
		):
			return {row.name: row.stock_qty for row in doc.items}
		return result
	proof_name = frappe.db.get_value(
		"Proof of Delivery", {"transport_trip_task": doc.execution_task, "docstatus": 1}, "name"
	)
	if (
		proof_name
		and doc.get("stock_entry")
		and frappe.db.get_value("Stock Entry", doc.stock_entry, "docstatus") == 1
	):
		from vuna_transport.services.execution.depot_custody_service import receipt_groups
		from vuna_transport.services.execution.shipment_diversion_exceptions import canonical_source

		proof = frappe.get_doc("Proof of Delivery", proof_name)
		for group in receipt_groups(proof):
			key = canonical_source(doc, proof, group.result.source_row)
			result[key] = result.get(key, 0) + group.qty * group.result.conversion_factor
	return {
		row.name: min(row.stock_qty, result.get(row.name, 0) + onward.get(row.name, 0)) for row in doc.items
	}


def customer_return_state(request):
	"""Aggregate partial vehicle returns; retain quality and commercial approval gates."""
	from vuna_transport.services.execution.return_service import (
		_from_stock_qty,
		_has_completed_commercial_resolution,
	)
	from vuna_transport.services.execution.stock_identity_service import get_delivery_note_item_batches

	items = {row.delivery_note_item: row for row in request.items}
	returned_stock = dict.fromkeys(items, 0.0)
	diverted_stock = dict.fromkeys(items, 0.0)
	names = frappe.get_all(
		"Delivery Note",
		filters={
			"vuna_return_request": request.name,
			"is_return": 1,
			"return_against": request.delivery_note,
			"company": request.company,
			"docstatus": ["!=", 2],
		},
		pluck="name",
	)
	if request.return_stock_document and request.return_stock_document not in names:
		names.append(request.return_stock_document)
	quality_complete = disposition_complete = True
	posted = drafts = 0
	for name in names:
		receipt = frappe.get_doc("Delivery Note", name)
		if (
			not receipt.is_return
			or receipt.company != request.company
			or receipt.customer != request.customer
			or receipt.return_against != request.delivery_note
		):
			frappe.throw(
				_("The return progress link does not belong to this customer and original delivery.")
			)
		if receipt.docstatus != 1:
			drafts += int(receipt.docstatus == 0)
			continue
		posted += 1
		inspection = frappe.db.get_value(
			"Return Inspection", {"sales_return": name, "docstatus": 1}, ["name", "stock_entry"], as_dict=True
		)
		root_names = frappe.get_all(
			"Shipment Diversion",
			filters={"sales_return": name, "previous_diversion": ["is", "not set"], "docstatus": 1},
			pluck="name",
		)
		disposed = {}
		for root_name in root_names:
			root = frappe.get_doc("Shipment Diversion", root_name)
			for item, qty in settled_stock(root).items():
				disposed[item] = disposed.get(item, 0) + qty
		for row in receipt.items:
			if row.dn_detail not in items:
				continue
			qty = abs(flt(row.stock_qty))
			returned_stock[row.dn_detail] += qty
			moved = min(qty, disposed.get(row.get("vuna_diversion_item"), 0))
			diverted_stock[row.dn_detail] += moved
			batches = get_delivery_note_item_batches(row)
			qi_filters = {
				"reference_type": "Delivery Note",
				"reference_name": name,
				"child_row_reference": row.name,
				"docstatus": 1,
				"status": ["in", ["Accepted", "Rejected"]],
			}
			if len(batches) == 1:
				qi_filters["batch_no"] = batches[0]
			inspected = bool(inspection) or (
				len(batches) <= 1 and bool(frappe.db.exists("Quality Inspection", qi_filters))
			)
			quality_complete = quality_complete and inspected
			redistributed = bool(
				inspection
				and inspection.stock_entry
				and frappe.db.get_value("Stock Entry", inspection.stock_entry, "docstatus") == 1
			)
			disposition_complete = disposition_complete and (redistributed or moved + 1e-6 >= qty)
	returned = {key: _from_stock_qty(qty, items[key]) for key, qty in returned_stock.items()}
	diverted_by_item = {
		items[key].name: _from_stock_qty(qty, items[key]) for key, qty in diverted_stock.items()
	}
	expected = sum(flt(row.requested_return_qty) for row in request.items)
	quantity = sum(returned.values())
	commercial = _has_completed_commercial_resolution(request)
	status = (
		"Draft"
		if request.docstatus == 0
		else "Cancelled"
		if request.docstatus == 2
		else (
			"Awaiting Sales Return"
			if quantity + 1e-6 < expected and not drafts
			else "Awaiting Return Submission"
			if drafts
			else "Awaiting Inspection"
			if not quality_complete
			else "Awaiting Disposition"
			if not disposition_complete
			else ("Closed" if request.status == "Closed" else "Resolved")
			if commercial
			else "Awaiting Commercial Resolution"
		)
	)
	return {
		"status": status,
		"expected_return_qty": expected,
		"returned_qty": quantity,
		"return_difference_qty": max(expected - quantity, 0),
		"returned_by_item": returned,
		"returned_stock_by_item": returned_stock,
		"diverted_qty": sum(diverted_by_item.values()),
		"diverted_by_item": diverted_by_item,
		"inspection_complete": quality_complete and bool(posted),
		"commercial_complete": commercial,
		"return_docstatus": 1 if posted else 0 if drafts else None,
	}


@frappe.whitelist()
def make_followup_diversion(source_name: str) -> dict:
	from vuna_transport.services.execution.shipment_diversion_service import (
		_get_shipment_diversion_context,
		_lock_diversion_source,
		available_diversion_stock,
	)

	require_doctype_permission("Shipment Diversion", "create")
	parent = frappe.get_doc("Shipment Diversion", source_name)
	require_document_permission(parent, "write")
	_lock_diversion_source(parent.return_request)
	parent = frappe.get_doc("Shipment Diversion", parent.name, for_update=True)
	if parent.docstatus != 1 or parent.destination_type != "Depot" or not parent.get("execution_task"):
		frappe.throw(
			_(
				"Use a submitted depot diversion with rejected goods still on the vehicle. Customer rejections start from their new Return Request."
			)
		)
	proof_name = frappe.db.get_value(
		"Proof of Delivery", {"transport_trip_task": parent.execution_task, "docstatus": 1}, "name"
	)
	if not proof_name:
		frappe.throw(_("Submit the depot POD before redirecting remaining cargo."))
	doc = frappe.new_doc("Shipment Diversion")
	for field in (
		"return_request",
		"company",
		"delivery_note",
		"proof_of_delivery",
		"delivery_trip",
		"vehicle",
		"driver",
		"vehicle_warehouse",
		"original_customer",
		"sales_return",
	):
		doc.set(field, parent.get(field))
	doc.previous_diversion, doc.previous_proof_of_delivery = parent.name, proof_name
	context = _get_shipment_diversion_context(parent.return_request, for_update=True, allow_resolved=True)
	candidates, reservations = followup_candidates(doc, context)
	for item in candidates.values():
		available, reserved_serials = available_diversion_stock(item, reservations)
		if available <= 0:
			continue
		serials = set(get_serial_nos(item.get("rejected_serial_no") or "")) - reserved_serials
		doc.append(
			"items",
			{
				**{
					field: item.get(field)
					for field in (
						"return_request_item",
						"delivery_note_item",
						"proof_of_delivery_item",
						"item_code",
						"uom",
						"stock_uom",
						"conversion_factor",
						"batch_no",
						"source_serial_and_batch_bundle",
						"source_diversion_item",
						"origin_diversion_item",
					)
				},
				"qty": available / item["conversion_factor"],
				"stock_qty": available,
				"available_qty": available / item["conversion_factor"],
				"available_serial_nos": "\n".join(sorted(serials)),
				"serial_no": "\n".join(sorted(serials)) if len(serials) == available else "",
			},
		)
	if not doc.items:
		frappe.throw(
			_(
				"No uncommitted, rejected goods remain on the vehicle. Depot-held and missing goods cannot be redirected from it."
			)
		)
	return doc.as_dict()
