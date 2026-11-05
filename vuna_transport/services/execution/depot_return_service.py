"""Depot rejection cases: operational returns, never customer credit documents."""

import math

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from vuna_transport.services.execution.depot_custody_service import rejection_custody_groups


def create_depot_return_from_pod(pod):
	if pod.docstatus != 1 or pod.proof_type != "Transfer Receipt":
		return None
	rejected = [row for row in pod.items if flt(row.rejected_qty) > 0]
	if not rejected:
		return None
	frappe.db.get_value("Proof of Delivery", pod.name, "name", for_update=True)
	if existing := frappe.db.get_value(
		"Return Request", {"proof_of_delivery": pod.name, "docstatus": ["!=", 2]}, "name"
	):
		return existing
	stock = frappe.get_doc("Stock Entry", pod.dispatch_stock_entry)
	task = frappe.get_doc("Transport Trip Task", pod.transport_trip_task)
	demand = frappe.get_doc("Transport Demand", task.transport_demand)
	groups = rejection_custody_groups(pod, stock.to_warehouse)
	warehouses = {group.warehouse for group in groups.values()}
	request = frappe.new_doc("Return Request")
	request.update(
		{
			"return_context": "Depot",
			"company": pod.company,
			"proof_of_delivery": pod.name,
			"source_stock_entry": stock.name,
			"source_warehouse": next(iter(warehouses)) if len(warehouses) == 1 else None,
			"rejecting_warehouse": demand.destination_warehouse,
			"return_warehouse": stock.from_warehouse,
			"delivery_trip": pod.delivery_trip,
			"return_source": "Depot Rejection",
			"initiated_by": "Driver",
			"return_method": "Driver Return" if warehouses == {stock.to_warehouse} else "Company Pickup",
			"custody": "Still With Vehicle"
			if warehouses == {stock.to_warehouse}
			else "Returned To Depot"
			if len(warehouses) == 1
			else "Split Custody",
			"status": "Draft",
			"return_remarks": pod.remarks,
		}
	)
	for group in groups.values():
		row = group.result
		request.append(
			"items",
			{
				"source_pod_item": row.name,
				"stock_entry_detail": row.source_row,
				"item_code": row.item_code,
				"item_name": row.item_name,
				"requested_return_qty": group.qty,
				"custody_warehouse": group.warehouse,
				"delivered_qty": row.delivered_qty,
				"uom": row.uom,
				"stock_uom": row.stock_uom,
				"conversion_factor": row.conversion_factor,
				"batch_no": row.batch_no,
				"serial_no": group.serial_no,
				"source_bundle": row.source_bundle,
				"return_reason": row.rejection_reason_remarks or row.rejection_reason or pod.remarks,
			},
		)
	# The submitted, permission-checked POD is the sole authority for this exact
	# draft. Drivers do not gain arbitrary Return Request or stock permissions.
	request.flags.ignore_permissions = True
	request.insert(ignore_permissions=True)
	pod.db_set("return_request", request.name, update_modified=False)
	return request.name


def validate_depot_return(request):
	if not request.proof_of_delivery:
		from vuna_transport.services.execution.manual_depot_return_service import validate_manual_depot_return

		validate_manual_depot_return(request)
		return
	frappe.db.get_value("Proof of Delivery", request.proof_of_delivery, "name", for_update=True)
	pod = frappe.get_doc("Proof of Delivery", request.proof_of_delivery)
	if pod.proof_type == "Unplanned Collection":
		from vuna_transport.services.execution.unplanned_pickup_service import validate_depot_collection_case

		validate_depot_collection_case(request, pod)
		return
	if (
		pod.docstatus != 1
		or pod.proof_type != "Transfer Receipt"
		or request.source_stock_entry != pod.dispatch_stock_entry
	):
		frappe.throw(_("Depot return must reference its submitted transfer POD and original dispatch."))
	if (
		request.customer
		or request.delivery_note
		or request.sales_invoice
		or request.credit_note
		or request.return_stock_document
	):
		frappe.throw(_("Depot returns use Stock Entries, not customer Sales Returns or credit documents."))
	if frappe.db.exists(
		"Return Request",
		{"proof_of_delivery": pod.name, "docstatus": ["!=", 2], "name": ["!=", request.name or ""]},
	):
		frappe.throw(_("This POD already has an active Return Request."))
	stock = frappe.get_doc("Stock Entry", pod.dispatch_stock_entry)
	task = frappe.get_doc("Transport Trip Task", pod.transport_trip_task)
	demand = frappe.get_doc("Transport Demand", task.transport_demand)
	if request.company != pod.company or stock.docstatus != 1:
		frappe.throw(_("Depot return company and posted dispatch must match its POD."))
	expected = rejection_custody_groups(pod, stock.to_warehouse)
	warehouses = {group.warehouse for group in expected.values()}
	request.source_warehouse = next(iter(warehouses)) if len(warehouses) == 1 else None
	request.rejecting_warehouse = demand.destination_warehouse
	request.return_context, request.return_source = "Depot", "Depot Rejection"
	request.initiation_date = request.initiation_date or nowdate()
	if not request.return_warehouse:
		request.return_warehouse = stock.from_warehouse
	warehouse = frappe.get_doc("Warehouse", request.return_warehouse)
	if (
		warehouse.company != pod.company
		or warehouse.disabled
		or warehouse.is_group
		or warehouse.name in warehouses
	):
		frappe.throw(
			_("Select an active same-company return warehouse different from current transit custody.")
		)
	seen = set()
	precision = frappe.get_precision("Return Request Item", "requested_return_qty")
	for row in request.items:
		# Old driver-only requests did not snapshot a per-row custody warehouse.
		key = (row.source_pod_item, row.get("custody_warehouse") or stock.to_warehouse)
		group = expected.get(key)
		source = group.result if group else None
		quantity = flt(row.requested_return_qty)
		if (
			not source
			or key in seen
			or not math.isfinite(quantity)
			or quantity <= 0
			or flt(quantity - group.qty, precision)
		):
			frappe.throw(_("Return expectations must preserve each POD rejection group and its quantity."))
		for field in (
			"item_code",
			"item_name",
			"uom",
			"stock_uom",
			"conversion_factor",
			"batch_no",
			"source_bundle",
		):
			row.set(field, source.get(field))
		row.stock_entry_detail, row.serial_no = source.source_row, group.serial_no
		row.custody_warehouse = group.warehouse
		row.return_reason = row.return_reason or source.rejection_reason_remarks or source.rejection_reason
		row.received_qty, row.remaining_qty = 0, quantity
		seen.add(key)
	if seen != set(expected):
		frappe.throw(_("Keep all rejected POD item groups in the Return Request."))
	state = depot_return_state(request)
	for row in request.items:
		row.received_qty = state["returned_by_item"].get(row.name, 0)
		row.remaining_qty = max(flt(row.requested_return_qty) - row.received_qty, 0)
	for field in ("status", "expected_return_qty", "returned_qty", "return_difference_qty"):
		request.set(field, state[field])


def depot_return_state(request):
	expected = sum(flt(row.requested_return_qty) for row in request.items)
	stock_received = dict(
		frappe.db.sql(
			"""select picked.vuna_return_request_item, sum(received.transfer_qty)
		from `tabStock Entry Detail` received
		join `tabStock Entry` receipt on receipt.name=received.parent and receipt.docstatus=1
		join `tabStock Entry Detail` picked on picked.name=received.ste_detail
		join `tabStock Entry` pickup on pickup.name=picked.parent and pickup.docstatus=1
		where pickup.vuna_return_pickup_proof is not null and pickup.add_to_transit=1
		and receipt.outgoing_stock_entry=pickup.name
		and received.t_warehouse=%(warehouse)s
		and picked.vuna_return_request_item in %(items)s group by picked.vuna_return_request_item""",
			{
				"items": tuple(row.name for row in request.items) or ("",),
				"warehouse": request.return_warehouse,
			},
		)
	)
	for item_name, quantity in frappe.db.sql(
		"""select d.vuna_return_request_item, sum(d.transfer_qty)
		from `tabStock Entry Detail` d join `tabStock Entry` s on s.name=d.parent
		where s.docstatus=1 and s.add_to_transit=0 and s.vuna_return_request=%s
		group by d.vuna_return_request_item""",
		request.name,
	):
		stock_received[item_name] = flt(stock_received.get(item_name)) + flt(quantity)
	by_item = {
		row.name: min(
			flt(row.requested_return_qty),
			flt(stock_received.get(row.name)) / (flt(row.conversion_factor) or 1),
		)
		for row in request.items
	}
	received = sum(by_item.values())
	progress = inspection_progress(request)
	quality = {"Accepted": {}, "Rejected": {}}
	for item_name, result, quantity in frappe.db.sql(
		"""select r.return_request_item, r.result, sum(r.inspection_qty)
		from `tabReturn Inspection Result` r join `tabReturn Inspection` i on i.name=r.parent
		where i.return_request=%s and i.docstatus=1
		group by r.return_request_item, r.result""",
		request.name,
	):
		if result in quality:
			quality[result][item_name] = flt(quantity)
	return {
		"status": "Cancelled"
		if request.docstatus == 2
		else "Draft"
		if request.docstatus == 0
		else "Resolved"
		if received >= expected and expected and progress["posted"] >= sum(stock_received.values())
		else "Awaiting Disposition"
		if received >= expected and expected and progress["inspected"] >= sum(stock_received.values())
		else "Awaiting Inspection"
		if received >= expected and expected
		else "Awaiting Receipt",
		"expected_return_qty": expected,
		"returned_qty": received,
		"return_difference_qty": max(expected - received, 0),
		"returned_by_item": by_item,
		"accepted_by_item": quality["Accepted"],
		"rejected_by_item": quality["Rejected"],
	}


def inspection_progress(request):
	"""Physical stock and submitted inspection/redistribution, never client counters."""
	inspected = posted = 0.0
	for row in frappe.get_all(
		"Return Inspection",
		filters={"return_request": request.name, "docstatus": 1},
		fields=["total_inspected_stock_qty", "stock_entry"],
	):
		inspected += flt(row.total_inspected_stock_qty)
		if row.stock_entry and frappe.db.get_value("Stock Entry", row.stock_entry, "docstatus") == 1:
			posted += flt(row.total_inspected_stock_qty)
	return {"inspected": inspected, "posted": posted}
