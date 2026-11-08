"""Resolve stop parties and allow only goods from their submitted stock documents."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt


def stop_context(trip, stop_name, *, allow_empty=False):
	stop = frappe.db.get_value(
		"Delivery Stop",
		{"name": stop_name, "parent": trip.name, "parenttype": "Delivery Trip"},
		["name", "customer", "address"],
		as_dict=True,
	)
	if not stop:
		frappe.throw(_("Select a collection stop belonging to this Delivery Trip."))
	warehouses = set()
	for task in frappe.get_all(
		"Transport Trip Task",
		filters={"delivery_trip": trip.name, "delivery_stop": stop.name, "status": ["!=", "Cancelled"]},
		fields=["task_type", "transport_demand", "transport_load"],
	):
		if task.task_type == "Transfer Receipt" and task.transport_demand:
			warehouse = frappe.db.get_value(
				"Transport Demand", task.transport_demand, "destination_warehouse"
			)
		elif task.task_type == "Return Pickup" and task.transport_load:
			warehouse = frappe.db.get_value("Transport Load", task.transport_load, "source_warehouse")
		else:
			continue
		if warehouse:
			warehouses.add(warehouse)
	if not warehouses and stop.address:
		warehouses.update(
			frappe.get_all(
				"Dynamic Link",
				filters={"parenttype": "Address", "parent": stop.address, "link_doctype": "Warehouse"},
				pluck="link_name",
			)
		)
	types = (["Delivery Note"] if stop.customer else []) + (["Stock Entry"] if warehouses else [])
	if not types and not allow_empty:
		frappe.throw(
			_("This stop needs a customer or an explicitly linked depot warehouse before collecting goods.")
		)
	return {
		"customer": stop.customer,
		"warehouses": sorted(warehouses),
		"reference_types": types,
		"location": stop.customer or stop.address or next(iter(sorted(warehouses)), ""),
	}


def reference_options(trip, stop_name, reference_type, reference_name, *, lock=False):
	context = stop_context(trip, stop_name)
	if reference_type not in context["reference_types"] or not reference_name:
		frappe.throw(_("Provide a supported Delivery Note or Stock Entry for this stop."))
	if lock:
		frappe.db.get_value(reference_type, reference_name, "name", for_update=True)
	source = frappe.get_doc(reference_type, reference_name)
	if source.docstatus != 1 or source.company != trip.company or source.get("is_return"):
		frappe.throw(_("Use a submitted, non-return source document in the trip company."))
	if reference_type == "Delivery Note" and source.customer != context["customer"]:
		frappe.throw(_("The Delivery Note must belong to this stop's customer."))
	options = []
	item_flags = {}
	for row in source.items:
		if reference_type == "Stock Entry" and row.t_warehouse not in context["warehouses"]:
			continue
		if flt(row.qty) <= 0 or not frappe.db.get_value("Item", row.item_code, "is_stock_item"):
			continue
		if reference_type == "Stock Entry":
			# Add-to-Transit is loading, not a receipt at a customer/depot.
			if (
				source.add_to_transit
				or frappe.db.get_value("Warehouse", row.t_warehouse, "warehouse_type") == "Transit"
			):
				continue
		factor = flt(row.conversion_factor)
		if factor <= 0:
			frappe.throw(_("The source document has an invalid UOM conversion factor."))
		groups = _identity_groups(row, reference_type)
		for batch, group in groups.items():
			stock_qty = group["stock_qty"]
			if stock_qty <= 0:
				continue
			if row.item_code not in item_flags:
				item_flags[row.item_code] = frappe.db.get_value(
					"Item", row.item_code, ["has_batch_no", "has_serial_no"]
				)
			has_batch, has_serial = item_flags[row.item_code]
			options.append(
				{
					"key": f"{row.name}:{batch or ''}",
					"reference_row": row.name,
					"row_idx": row.get("idx") or 0,
					"name": row.item_code,
					"item_name": row.item_name or row.item_code,
					"uom": row.uom,
					"uoms": [row.uom],
					"stock_uom": row.stock_uom,
					"conversion_factor": factor,
					"qty": stock_qty / factor,
					"stock_qty": stock_qty,
					"batch_no": batch or None,
					"serial_no": "\n".join(group["serials"]),
					"has_batch_no": has_batch,
					"has_serial_no": has_serial,
					"warehouse": row.t_warehouse if reference_type == "Stock Entry" else None,
				}
			)
	if not options:
		frappe.throw(_("This document has no received stock rows for the selected stop."))
	return {
		**context,
		"party_type": "Customer" if reference_type == "Delivery Note" else "Depot",
		"items": options,
	}


def _identity_groups(row, reference_type):
	groups = defaultdict(lambda: {"stock_qty": 0, "serials": []})
	if row.serial_and_batch_bundle:
		bundle = frappe.get_doc("Serial and Batch Bundle", row.serial_and_batch_bundle)
		for entry in bundle.entries:
			group = groups[entry.batch_no or ""]
			group["stock_qty"] += abs(flt(entry.qty))
			if entry.serial_no:
				group["serials"].append(entry.serial_no)
	else:
		group = groups[row.batch_no or ""]
		group["stock_qty"] = abs(
			flt(row.stock_qty if reference_type == "Delivery Note" else row.transfer_qty)
		)
		group["serials"] = [value.strip() for value in (row.serial_no or "").splitlines() if value.strip()]
	return groups


def validate_reference_rows(proof, reference):
	options = {(row["reference_row"], row["batch_no"] or ""): row for row in reference["items"]}
	quantities = defaultdict(float)
	serials = set()
	for row in proof.items:
		key = (row.get("collection_reference_row"), row.batch_no or "")
		source = options.get(key)
		if not source or row.item_code != source["name"] or row.uom != source["uom"]:
			frappe.throw(_("Select an item row, batch and UOM from the referenced document."))
		if reference["party_type"] == "Depot":
			if proof.get("collection_warehouse") and proof.collection_warehouse != source["warehouse"]:
				frappe.throw(
					_("A collection must refer to one depot warehouse; record other warehouses separately.")
				)
			proof.collection_warehouse = source["warehouse"]
		if not set((row.serial_no or "").splitlines()).issubset(set(source["serial_no"].splitlines())):
			frappe.throw(_("Collected serial numbers must belong to the referenced stock row."))
		quantities[key] += flt(row.stock_qty)
		serials.update((row.serial_no or "").splitlines())
	# Reports reserve their source quantities; cancelled reports do not count.
	for row in frappe.db.sql(
		"""select i.collection_reference_row, i.batch_no, i.stock_qty, i.serial_no
		from `tabProof of Delivery Item` i join `tabProof of Delivery` p on p.name=i.parent
		where p.proof_type='Unplanned Collection' and p.docstatus!=2 and p.name!=%(proof)s
		and p.collection_reference_doctype=%(type)s and p.collection_reference_name=%(source)s""",
		{
			"proof": proof.name or "",
			"type": proof.collection_reference_doctype,
			"source": proof.collection_reference_name,
		},
		as_dict=True,
	):
		key = (row.collection_reference_row, row.batch_no or "")
		if key in quantities:
			quantities[key] += flt(row.stock_qty)
		if serials.intersection((row.serial_no or "").splitlines()):
			frappe.throw(_("A collected serial number has already been reported against this document."))
	for key, quantity in quantities.items():
		if (
			flt(
				quantity - options[key]["stock_qty"],
				frappe.get_precision("Proof of Delivery Item", "stock_qty"),
			)
			> 0
		):
			frappe.throw(_("Collected quantities exceed the referenced document row/batch quantity."))
