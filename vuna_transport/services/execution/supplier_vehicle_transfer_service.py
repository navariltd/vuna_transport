"""Ordinary Material Transfer from purchased vehicle stock; never End Transit."""

import math

import frappe
from frappe import _
from frappe.utils import flt

from vuna_transport.services.execution.transfer_proof_service import stock_identities
from vuna_transport.services.shared.security_service import (
	require_doctype_permission,
	require_document_permission,
)


def receiving_warehouse(name, company):
	warehouse = frappe.get_doc("Warehouse", name)
	require_document_permission(warehouse, "read")
	if (
		warehouse.company != company
		or warehouse.disabled
		or warehouse.is_group
		or warehouse.warehouse_type == "Transit"
	):
		frappe.throw(
			_("Choose an active same-company receiving warehouse, not a group or transit warehouse.")
		)
	if warehouse.get("is_rejected_warehouse") and "nl_vms" in frappe.get_installed_apps():
		frappe.throw(
			_("Choose an ordinary receiving/holding warehouse, not an automatic write-off warehouse.")
		)


def receipt_groups(receipt, exclude_entry=None):
	"""Receipt lot identity minus linked drafts/posted transfers, in Stock UOM."""
	groups = {}
	for row in receipt.items:
		values = frappe._dict(row.as_dict())
		values.transfer_qty = row.stock_qty
		for batch, identity in stock_identities(values).items():
			groups[(row.name, batch)] = frappe._dict(
				source=row, batch=batch, qty=identity["stock_qty"], serials=set(identity["serials"])
			)
	for value in frappe.db.sql(
		"""select d.* from `tabStock Entry Detail` d join `tabStock Entry` s on s.name=d.parent
		where s.vuna_supplier_purchase_receipt=%s and s.docstatus in (0,1) and s.name!=%s""",
		(receipt.name, exclude_entry or ""),
		as_dict=True,
	):
		for batch, identity in stock_identities(value).items():
			group = groups.get((value.vuna_supplier_receipt_item, batch))
			if group:
				group.qty -= identity["stock_qty"]
				group.serials.difference_update(identity["serials"])
	return groups


def transfer_context(receipt_name):
	receipt = frappe.get_doc("Purchase Receipt", receipt_name)
	require_document_permission(receipt, "read")
	frappe.db.get_value("Purchase Receipt", receipt.name, "name", for_update=True)
	receipt.reload()
	if (
		receipt.docstatus != 1
		or receipt.is_return
		or not receipt.get("vuna_pickup_purchase_order")
		or not receipt.get("vuna_pickup_transit_warehouse")
	):
		frappe.throw(
			_(
				"Submit the supplier Purchase Receipt into vehicle stock before transferring it to a warehouse."
			)
		)
	# Supplier returns are a separate workflow. Do not allocate original receipt
	# quantities again after goods have been returned (or reserved for return).
	if frappe.db.exists(
		"Purchase Receipt", {"return_against": receipt.name, "is_return": 1, "docstatus": ["!=", 2]}
	) or frappe.db.sql(
		"""select p.name from `tabPurchase Invoice` p join `tabPurchase Invoice Item` i on i.parent=p.name
		where p.docstatus in (0,1) and p.is_return=1 and p.update_stock=1 and i.purchase_receipt=%s limit 1""",
		receipt.name,
	):
		frappe.throw(
			_(
				"Reconcile the linked supplier return before transferring this Purchase Receipt's vehicle stock."
			)
		)
	return receipt


@frappe.whitelist()
def create_vehicle_material_transfer(source_name: str, target_warehouse: str) -> str:
	from erpnext.stock.serial_batch_bundle import SerialBatchCreation

	from vuna_transport.services.execution.allocation_transfer_service import set_allocation_stock_defaults

	require_doctype_permission("Stock Entry", "create")
	receipt = transfer_context(source_name)
	receiving_warehouse(target_warehouse, receipt.company)
	if existing := frappe.db.get_value(
		"Stock Entry", {"vuna_supplier_purchase_receipt": receipt.name, "docstatus": 0}, "name"
	):
		require_document_permission(frappe.get_doc("Stock Entry", existing), "read")
		return existing
	entry = frappe.new_doc("Stock Entry")
	entry.company = receipt.company
	entry.stock_entry_type = entry.purpose = "Material Transfer"
	entry.from_warehouse, entry.to_warehouse = receipt.vuna_pickup_transit_warehouse, target_warehouse
	entry.vuna_supplier_purchase_receipt = receipt.name
	entry.add_to_transit = 0
	for group in receipt_groups(receipt).values():
		if flt(group.qty, frappe.get_precision("Stock Entry Detail", "transfer_qty")) <= 0:
			continue
		source = group.source
		row = entry.append(
			"items",
			{
				"item_code": source.item_code,
				"qty": group.qty / source.conversion_factor,
				"uom": source.uom,
				"stock_uom": source.stock_uom,
				"conversion_factor": source.conversion_factor,
				"transfer_qty": group.qty,
				"s_warehouse": entry.from_warehouse,
				"t_warehouse": target_warehouse,
				"vuna_supplier_receipt_item": source.name,
				"use_serial_batch_fields": 0,
			},
		)
		if group.batch or group.serials:
			bundle = SerialBatchCreation(
				frappe._dict(
					item_code=row.item_code,
					company=entry.company,
					warehouse=row.s_warehouse,
					type_of_transaction="Outward",
					voucher_type="Stock Entry",
					qty=-group.qty,
					do_not_submit=True,
				)
			).make_serial_and_batch_bundle(
				serial_nos=sorted(group.serials), batch_nos={group.batch: group.qty} if group.batch else None
			)
			row.serial_and_batch_bundle = bundle.name
	if not entry.items:
		frappe.throw(
			_(
				"All received supplier quantities have already been transferred or reserved in a draft transfer."
			)
		)
	allocation = frappe.get_doc("Vehicle Allocation", receipt.vuna_pickup_allocation)
	require_document_permission(allocation, "read")
	set_allocation_stock_defaults(entry, allocation)
	entry.insert()
	return entry.name


def validate_vehicle_material_transfer(doc, method=None):
	previous = doc.get_doc_before_save()
	if previous and previous.get("vuna_supplier_purchase_receipt") != doc.get(
		"vuna_supplier_purchase_receipt"
	):
		frappe.throw(_("Supplier receipt linkage cannot be changed on a saved Stock Entry."))
	if not doc.get("vuna_supplier_purchase_receipt"):
		return
	receipt = transfer_context(doc.vuna_supplier_purchase_receipt)
	if (
		doc.company != receipt.company
		or doc.purpose != "Material Transfer"
		or doc.add_to_transit
		or doc.outgoing_stock_entry
		or doc.from_warehouse not in (None, "", receipt.vuna_pickup_transit_warehouse)
	):
		frappe.throw(
			_(
				"Transfer purchased vehicle stock with a normal Material Transfer, not Add-to-Transit or End Transit."
			)
		)
	groups = receipt_groups(receipt, doc.name)
	seen = set()
	for row in doc.items:
		receiving_warehouse(row.t_warehouse, receipt.company)
		identities = stock_identities(row)
		if len(identities) != 1:
			frappe.throw(_("Keep one original receipt batch group per transfer row."))
		batch, identity = next(iter(identities.items()))
		key = (row.vuna_supplier_receipt_item, batch)
		group = groups.get(key)
		source = group.source if group else None
		stock_qty = flt(row.transfer_qty)
		if (
			not source
			or key in seen
			or not math.isfinite(stock_qty)
			or stock_qty <= 0
			or row.item_code != source.item_code
			or row.uom != source.uom
			or row.stock_uom != source.stock_uom
			or flt(row.conversion_factor) != flt(source.conversion_factor)
			or row.s_warehouse != receipt.vuna_pickup_transit_warehouse
			or flt(stock_qty - group.qty, row.precision("transfer_qty")) > 0
			or flt(identity["stock_qty"] - stock_qty, row.precision("transfer_qty"))
		):
			frappe.throw(
				_("Vehicle transfer rows must retain receipt item, UOM, batch and available quantity.")
			)
		serials = identity["serials"]
		if (
			len(serials) != len(set(serials))
			or not set(serials).issubset(group.serials)
			or (group.serials and len(serials) != stock_qty)
		):
			frappe.throw(
				_(
					"Select exactly the received supplier serial numbers matching the transferred stock quantity."
				)
			)
		seen.add(key)
