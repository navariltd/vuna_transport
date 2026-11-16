"""Operational custody and quality orchestration for customer returns V2."""

from collections import defaultdict

import frappe
from erpnext.utilities.transaction_base import validate_uom_is_integer
from frappe import _
from frappe.utils import flt, now_datetime

from vuna_transport.services.shared.security_service import (
	require_doctype_permission,
	require_document_permission,
)

RETURN_PROCESS_V2 = "Custody and Inspection"


def is_v2_return_request(request) -> bool:
	return request.get("process_version") == RETURN_PROCESS_V2


def _validate_return_inspection_sales_return(inspection) -> None:
	if inspection.sales_return and frappe.db.exists(
		"Shipment Diversion", {"sales_return": inspection.sales_return, "docstatus": ["!=", 2]}
	):
		frappe.throw(
			_(
				"This vehicle receipt is committed to a Shipment Diversion. Record its Quality Inspection on the Sales Return or inspect the receiving depot's return case; do not move the same stock again."
			)
		)
	sales_return = frappe.get_doc("Delivery Note", inspection.sales_return)
	require_document_permission(sales_return, "read")
	if sales_return.docstatus != 1 or not sales_return.is_return:
		frappe.throw(_("Sales Return {0} must be submitted before inspection.").format(sales_return.name))
	request_name = sales_return.vuna_return_request
	if not request_name:
		frappe.throw(_("Sales Return {0} must be linked to a Return Request.").format(sales_return.name))
	request = frappe.get_doc("Return Request", request_name)
	inspection.return_request = request.name
	inspection.company = sales_return.company
	inspection.customer = sales_return.customer
	warehouses = {row.warehouse for row in sales_return.items if row.warehouse}
	if len(warehouses) != 1:
		frappe.throw(_("All Sales Return rows must use one quarantine warehouse before inspection."))
	inspection.source_warehouse = next(iter(warehouses))
	if inspection.docstatus == 1:
		if inspection.accepted_warehouse:
			_validate_warehouse(inspection.accepted_warehouse, inspection.company)
		if inspection.rejected_warehouse:
			_validate_warehouse(inspection.rejected_warehouse, inspection.company)
			_validate_rejected_warehouse(inspection.rejected_warehouse)
	if not inspection.results:
		frappe.throw(_("Add at least one inspection result group."))

	source_items = {row.name: row for row in sales_return.items if flt(row.stock_qty) < 0}
	inspected_by_item = defaultdict(float)
	inspected_serials_by_item = defaultdict(set)
	quality_inspections = set()
	from vuna_transport.services.execution.return_inspection_quality_service import (
		validate_result_quality_inspection,
	)

	accepted = rejected = 0.0
	for row in inspection.results:
		source = source_items.get(row.delivery_note_item)
		if not source and row.item_code:
			candidates = [
				item
				for item in source_items.values()
				if item.item_code == row.item_code and (not row.batch_no or item.batch_no == row.batch_no)
			]
			if len(candidates) == 1:
				source = candidates[0]
				row.delivery_note_item = source.name
			elif len(candidates) > 1:
				frappe.throw(_("Row #{0}: Select the batch for {1}.").format(row.idx, row.item_code))
		if not source:
			frappe.throw(_("Row #{0}: Select an item from the Sales Return.").format(row.idx))
		if row.item_code and row.item_code != source.item_code:
			frappe.throw(_("Row #{0}: Item does not match the selected Sales Return row.").format(row.idx))
		row.item_code = source.item_code
		row.stock_uom = source.stock_uom
		row.received_stock_qty = abs(flt(source.stock_qty))
		row.serial_and_batch_bundle = source.serial_and_batch_bundle
		row.batch_no = source.batch_no
		if flt(row.inspection_qty) <= 0:
			frappe.throw(_("Row #{0}: Inspection quantity must be greater than zero.").format(row.idx))
		if row.result == "Rejected" and not row.reason:
			frappe.throw(_("Row #{0}: Rejection Reason is required.").format(row.idx))
		if inspection.docstatus == 1 and not row.target_warehouse:
			frappe.throw(_("Row #{0}: Select a Target Warehouse.").format(row.idx))
		if row.target_warehouse:
			_validate_warehouse(row.target_warehouse, inspection.company)
			if row.result == "Rejected":
				_validate_rejected_warehouse(row.target_warehouse)
		validate_result_quality_inspection(inspection, row, quality_inspections)
		serials = {serial for serial in (source.serial_no or "").splitlines() if serial}
		row_serials = [serial for serial in (row.serial_no or "").splitlines() if serial]
		if serials:
			if len(row_serials) != int(flt(row.inspection_qty)) or not set(row_serials).issubset(serials):
				frappe.throw(_("Row #{0}: Serial numbers must match the Sales Return.").format(row.idx))
			if inspected_serials_by_item[source.name].intersection(row_serials):
				frappe.throw(_("Row #{0}: A serial number cannot appear in multiple groups.").format(row.idx))
			inspected_serials_by_item[source.name].update(row_serials)
		elif row_serials:
			frappe.throw(_("Row #{0}: This Sales Return item is not serialized.").format(row.idx))
		inspected_by_item[source.name] += flt(row.inspection_qty)
		if row.result == "Accepted":
			accepted += flt(row.inspection_qty)
		elif row.result == "Rejected":
			rejected += flt(row.inspection_qty)
		else:
			frappe.throw(_("Row #{0}: Select Accepted or Rejected.").format(row.idx))

	for source in source_items.values():
		expected_qty = abs(flt(source.stock_qty))
		balance = inspected_by_item.get(source.name, 0) - expected_qty
		if balance > 1e-6 or (inspection.docstatus == 1 and abs(balance) > 1e-6):
			frappe.throw(
				_("Inspection quantity for {0} must equal received quantity {1} {2}.").format(
					source.item_code, expected_qty, source.stock_uom
				)
			)
	serial_total = sum(abs(flt(row.stock_qty)) for row in source_items.values())
	inspection.total_received_stock_qty = serial_total
	inspection.total_inspected_stock_qty = accepted + rejected
	inspection.accepted_stock_qty = accepted
	inspection.rejected_stock_qty = rejected
	inspection.pending_stock_qty = max(serial_total - accepted - rejected, 0)
	validate_uom_is_integer(inspection, "stock_uom", "inspection_qty", "Return Inspection Result")


def validate_return_inspection(inspection) -> None:
	if inspection.get("receiving_stock_entry"):
		from vuna_transport.services.execution.depot_return_inspection_service import validate_inspection

		validate_inspection(inspection)
		return
	_validate_return_inspection_sales_return(inspection)


def validate_return_inspection_cancellation(inspection) -> None:
	if not inspection.stock_entry:
		return
	docstatus = frappe.db.get_value("Stock Entry", inspection.stock_entry, "docstatus")
	if docstatus == 0:
		frappe.throw(
			_("Delete draft Stock Entry {0} before cancelling this inspection.").format(
				inspection.stock_entry
			)
		)
	if docstatus == 1:
		frappe.throw(
			_("Cancel Stock Entry {0} before cancelling this inspection.").format(inspection.stock_entry)
		)


def validate_sales_return_lineage(sales_return, request) -> None:
	"""Validate Sales Return lineage and aggregate split rows against the request."""
	request_items = {row.delivery_note_item: row for row in request.items}
	receiving_warehouse = request.return_warehouse
	if sales_return.get("vuna_shipment_diversion"):
		diversion = frappe.get_doc("Shipment Diversion", sales_return.vuna_shipment_diversion)
		if diversion.docstatus != 1 or diversion.return_request != request.name:
			frappe.throw(_("Vehicle returns require an approved diversion for this Return Request."))
		receiving_warehouse = diversion.vehicle_warehouse
	totals = defaultdict(float)
	for row in sales_return.items:
		request_item = request_items.get(row.dn_detail)
		if not request_item:
			frappe.throw(
				_("Row #{0}: Delivery Note lineage does not match the Return Request.").format(row.idx)
			)
		if row.warehouse != receiving_warehouse:
			frappe.throw(
				_("Row #{0}: Returned stock must enter the Return Warehouse {1}.").format(
					row.idx, receiving_warehouse
				)
			)
		totals[row.dn_detail] += abs(flt(row.stock_qty))
	for delivery_note_item, quantity in totals.items():
		request_item = request_items[delivery_note_item]
		request_qty = flt(
			request_item.requested_return_qty,
			frappe.get_precision("Return Request Item", "requested_return_qty") or 3,
		)
		expected = flt(
			request_qty * (flt(request_item.conversion_factor) or 1),
			frappe.get_precision("Delivery Note Item", "stock_qty") or 3,
		)
		if quantity > expected + 1e-6:
			frappe.throw(
				_("Sales Return quantity for {0} cannot exceed the requested return quantity.").format(
					request_item.item_code
				)
			)


def validate_return_stock_entry(doc, method=None) -> None:
	"""Validate the ERPNext bundle selected for a Return Inspection transfer."""
	inspection_name = frappe.db.get_value(
		"Return Inspection", {"stock_entry": doc.name, "docstatus": 1}, "name"
	)
	if not inspection_name:
		return
	inspection = frappe.get_doc("Return Inspection", inspection_name)
	if inspection.get("receiving_stock_entry"):
		from vuna_transport.services.execution.depot_return_inspection_service import validate_stock_entry

		validate_stock_entry(doc, inspection)
		return
	if doc.company != inspection.company or doc.purpose != "Material Transfer":
		frappe.throw(
			_("Stock Entry must remain a Material Transfer for Company {0}.").format(inspection.company)
		)

	expected_qty = defaultdict(float)
	allowed_batches = defaultdict(lambda: defaultdict(float))
	allowed_serials = defaultdict(set)
	tracked_source_bundles = set()
	for result in inspection.results:
		key = (result.item_code, result.target_warehouse)
		expected_qty[key] += flt(result.inspection_qty)
		if result.serial_and_batch_bundle:
			if result.serial_and_batch_bundle in tracked_source_bundles:
				continue
			tracked_source_bundles.add(result.serial_and_batch_bundle)
			bundle = frappe.get_doc("Serial and Batch Bundle", result.serial_and_batch_bundle)
			for entry in bundle.entries:
				if entry.batch_no:
					allowed_batches[result.item_code][entry.batch_no] += abs(flt(entry.qty))
				if entry.serial_no:
					allowed_serials[result.item_code].add(entry.serial_no)
		else:
			if result.batch_no:
				allowed_batches[result.item_code][result.batch_no] += flt(result.inspection_qty)
			allowed_serials[result.item_code].update(
				serial for serial in (result.serial_no or "").splitlines() if serial
			)

	actual_qty = defaultdict(float)
	actual_batches = defaultdict(lambda: defaultdict(float))
	actual_serials = defaultdict(set)
	bundle_ids = [row.serial_and_batch_bundle for row in doc.items if row.serial_and_batch_bundle]
	bundle_entries = defaultdict(list)
	if bundle_ids:
		for entry in frappe.get_all(
			"Serial and Batch Entry",
			filters={"parent": ["in", bundle_ids]},
			fields=["parent", "batch_no", "serial_no", "qty"],
		):
			bundle_entries[entry.parent].append(entry)

	for row in doc.items:
		if row.s_warehouse != inspection.source_warehouse:
			frappe.throw(
				_("Row #{0}: Source Warehouse must remain {1}.").format(row.idx, inspection.source_warehouse)
			)
		key = (row.item_code, row.t_warehouse)
		actual_qty[key] += abs(flt(row.transfer_qty or row.qty))
		if row.serial_and_batch_bundle:
			for entry in bundle_entries[row.serial_and_batch_bundle]:
				if entry.batch_no:
					actual_batches[row.item_code][entry.batch_no] += abs(flt(entry.qty))
				if entry.serial_no:
					actual_serials[row.item_code].add(entry.serial_no)
		else:
			if row.batch_no:
				actual_batches[row.item_code][row.batch_no] += abs(flt(row.transfer_qty or row.qty))
			actual_serials[row.item_code].update(
				serial for serial in (row.serial_no or "").splitlines() if serial
			)

	if set(actual_qty) != set(expected_qty) or any(
		abs(actual_qty[key] - quantity) > 1e-6 for key, quantity in expected_qty.items()
	):
		frappe.throw(
			_("Stock Entry quantities and warehouses must match Return Inspection {0}.").format(
				inspection.name
			)
		)

	for item_code, batches in actual_batches.items():
		for batch_no, quantity in batches.items():
			if quantity > allowed_batches[item_code].get(batch_no, 0) + 1e-6:
				frappe.throw(
					_("Batch {0} for {1} exceeds the quantity returned for this inspection.").format(
						batch_no, item_code
					)
				)
	for item_code, serials in actual_serials.items():
		unexpected = serials - allowed_serials[item_code]
		if unexpected:
			frappe.throw(
				_("Serial number(s) {0} were not part of the returned stock for {1}.").format(
					", ".join(sorted(unexpected)), item_code
				)
			)


@frappe.whitelist()
def create_return_inspection_from_sales_return(sales_return: str) -> str:
	target = frappe.get_doc("Delivery Note", sales_return)
	require_document_permission(target, "read")
	require_doctype_permission("Return Inspection", "create")
	if target.docstatus != 1 or not target.is_return:
		frappe.throw(_("Submit Sales Return {0} before inspection.").format(target.name))
	request_name = target.vuna_return_request
	if not request_name:
		frappe.throw(_("Sales Return {0} must be linked to a Return Request.").format(target.name))
	existing = frappe.db.get_value(
		"Return Inspection", {"sales_return": target.name, "docstatus": ["!=", 2]}, "name"
	)
	if existing:
		return existing
	warehouses = {row.warehouse for row in target.items if row.warehouse}
	if len(warehouses) != 1:
		frappe.throw(_("All Sales Return rows must use one quarantine warehouse before inspection."))
	inspection = frappe.new_doc("Return Inspection")
	inspection.inspection_required = frappe.db.get_single_value(
		"Transport Settings", "require_quality_inspection_for_return_inspection"
	)
	inspection.update(
		{
			"sales_return": target.name,
			"return_request": request_name,
			"company": target.company,
			"customer": target.customer,
			"source_warehouse": next(iter(warehouses)),
			"inspected_on": now_datetime(),
			"inspected_by": frappe.session.user,
		}
	)
	for item in target.items:
		if flt(item.stock_qty) >= 0:
			continue
		inspection.append(
			"results",
			{
				"delivery_note_item": item.name,
				"item_code": item.item_code,
				"received_stock_qty": abs(flt(item.stock_qty)),
				"inspection_qty": abs(flt(item.stock_qty)),
				"stock_uom": item.stock_uom,
				"serial_and_batch_bundle": item.serial_and_batch_bundle,
				"result": "Accepted",
				"target_warehouse": None,
				"batch_no": item.batch_no,
				"serial_no": item.serial_no,
			},
		)
	inspection.insert()
	return inspection.name


@frappe.whitelist()
def get_sales_return_inspection_item_options(
	sales_return: str, item_code: str | None = None, batch_no: str | None = None
) -> list[dict]:
	target = frappe.get_doc("Delivery Note", sales_return)
	require_document_permission(target, "read")
	filters = {"parent": target.name, "parenttype": "Delivery Note", "stock_qty": ["<", 0]}
	if item_code:
		filters["item_code"] = item_code
	if batch_no:
		filters["batch_no"] = batch_no
	return frappe.get_all(
		"Delivery Note Item",
		filters=filters,
		fields=[
			"name",
			"item_code",
			"item_name",
			"stock_uom",
			"stock_qty",
			"batch_no",
			"serial_no",
			"serial_and_batch_bundle",
		],
		order_by="idx asc",
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_sales_return_inspection_item_query(
	doctype: str, txt: str, searchfield: str, start: int, page_len: int, filters: dict | str | None
) -> list[dict]:
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	if not filters.get("sales_return"):
		return []
	target = frappe.get_doc("Delivery Note", filters["sales_return"])
	require_document_permission(target, "read")
	return frappe.db.sql(
		"""
		select distinct item_code as value, item_name as description
		from `tabDelivery Note Item`
		where parent = %(parent)s and parenttype = 'Delivery Note'
			and stock_qty < 0 and item_code like %(txt)s
		order by item_code limit %(start)s, %(page_len)s
		""",
		{"parent": target.name, "txt": f"%{txt}%", "start": int(start), "page_len": int(page_len)},
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_sales_return_inspection_batch_query(
	doctype: str, txt: str, searchfield: str, start: int, page_len: int, filters: dict | str | None
) -> list[dict]:
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	if not filters.get("sales_return"):
		return []
	target = frappe.get_doc("Delivery Note", filters["sales_return"])
	require_document_permission(target, "read")
	return frappe.db.sql(
		"""
		select distinct batch_no as value, batch_no as description
		from `tabDelivery Note Item`
		where parent = %(parent)s and parenttype = 'Delivery Note'
			and stock_qty < 0 and batch_no is not null and batch_no != ''
			and batch_no like %(txt)s
			and (%(item_code)s is null or item_code = %(item_code)s)
		order by batch_no limit %(start)s, %(page_len)s
		""",
		{
			"parent": target.name,
			"item_code": filters.get("item_code") or None,
			"txt": f"%{txt}%",
			"start": int(start),
			"page_len": int(page_len),
		},
	)


@frappe.whitelist()
def create_return_stock_entry(return_inspection: str) -> str:
	frappe.db.get_value("Return Inspection", return_inspection, "name", for_update=True)
	inspection = frappe.get_doc("Return Inspection", return_inspection)
	require_document_permission(inspection, "read")
	require_doctype_permission("Stock Entry", "create")
	if inspection.docstatus != 1:
		frappe.throw(_("Submit Return Inspection {0} first.").format(inspection.name))
	if inspection.stock_entry:
		status = frappe.db.get_value("Stock Entry", inspection.stock_entry, "docstatus")
		if status is not None and status != 2:
			return inspection.stock_entry
	if inspection.get("receiving_stock_entry"):
		from vuna_transport.services.execution.depot_return_inspection_service import create_stock_entry

		return create_stock_entry(inspection)

	grouped = defaultdict(float)
	for row in inspection.results:
		key = (row.item_code, row.target_warehouse, row.stock_uom)
		grouped[key] += flt(row.inspection_qty)

	entry = frappe.new_doc("Stock Entry")
	entry.update(
		{
			"company": inspection.company,
			"stock_entry_type": "Material Transfer",
			"purpose": "Material Transfer",
		}
	)
	for (item_code, target_warehouse, stock_uom), quantity in grouped.items():
		entry.append(
			"items",
			{
				"item_code": item_code,
				"s_warehouse": inspection.source_warehouse,
				"t_warehouse": target_warehouse,
				"qty": quantity,
				"transfer_qty": quantity,
				"uom": stock_uom,
				"stock_uom": stock_uom,
				"conversion_factor": 1,
				"use_serial_batch_fields": 0,
			},
		)
	entry.insert()
	inspection.db_set("stock_entry", entry.name, update_modified=True)
	return entry.name


def sync_return_inspection_from_stock_entry(doc, method=None) -> None:
	inspection = frappe.db.get_value(
		"Return Inspection", {"stock_entry": doc.name}, ["name", "return_request"], as_dict=True
	)
	if not inspection:
		return
	status = "Submitted"
	frappe.db.set_value("Return Inspection", inspection.name, "status", status, update_modified=False)
	from vuna_transport.services.execution.return_service import recalculate_return_request

	recalculate_return_request(inspection.return_request)


def prepare_return_stock_entry_removal(doc, method=None) -> None:
	"""Release the V2 backlink before Frappe evaluates cancellation links."""
	inspection = frappe.db.get_value(
		"Return Inspection", {"stock_entry": doc.name}, ["name", "return_request"], as_dict=True
	)
	if not inspection:
		return
	frappe.db.set_value(
		"Return Inspection",
		inspection.name,
		{"stock_entry": None, "status": "Submitted"},
		update_modified=False,
	)
	from vuna_transport.services.execution.return_service import recalculate_return_request

	recalculate_return_request(inspection.return_request)


def prepare_return_quality_inspection_removal(doc, method=None) -> None:
	"""Clear optional result-group evidence links before cancelling a Quality Inspection."""
	rows = frappe.get_all(
		"Return Inspection Result",
		filters={"quality_inspection": doc.name},
		fields=["name", "parent", "docstatus"],
	)
	for row in rows:
		if row.docstatus == 1:
			frappe.throw(
				_("Cancel Return Inspection {0} before removing its Quality Inspection evidence.").format(
					row.parent
				)
			)
		frappe.db.set_value(
			"Return Inspection Result", row.name, "quality_inspection", None, update_modified=False
		)


def _validate_warehouse(warehouse: str, company: str) -> None:
	values = frappe.db.get_value("Warehouse", warehouse, ["company", "is_group", "disabled"], as_dict=True)
	if not values or values.company != company or values.is_group or values.disabled:
		frappe.throw(
			_("Warehouse {0} must be an active non-group warehouse for {1}.").format(warehouse, company)
		)


def _validate_rejected_warehouse(warehouse: str) -> None:
	if not frappe.db.get_value("Warehouse", warehouse, "is_rejected_warehouse"):
		frappe.throw(_("Warehouse {0} must be marked as a Rejected Warehouse.").format(warehouse))
