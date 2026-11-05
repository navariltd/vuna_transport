"""Warehouse-only stock/QI preparation before a driver confirms physical handover."""

import frappe
from frappe import _

from vuna_transport.services.shared.security_service import (
	require_doctype_permission,
	require_document_permission,
)


@frappe.whitelist()
def prepare_stock_entry(proof_name: str) -> str:
	from vuna_transport.services.execution.return_pickup_service import (
		post_pickup_stock,
		validate_pickup_proof,
	)
	from vuna_transport.services.execution.transfer_proof_service import (
		_make_receipt_stock_entry,
		validate_proof,
	)

	require_doctype_permission("Stock Entry", "create")
	proof = frappe.get_doc("Proof of Delivery", proof_name)
	require_document_permission(proof, "write")
	frappe.db.get_value("Proof of Delivery", proof.name, "name", for_update=True)
	proof.reload()
	if proof.docstatus != 0:
		frappe.throw(_("Save a draft pickup or receiving POD before preparing its stock/QI documents."))
	if proof.proof_type == "Return Pickup":
		validate_pickup_proof(proof)
		name = post_pickup_stock(proof, prepare_only=True)
		if not name:
			frappe.throw(_("This pickup does not require a warehouse stock transfer."))
		return name
	if proof.proof_type == "Transfer Receipt":
		validate_proof(proof)
		entry = _make_receipt_stock_entry(proof, allow_draft_proof=True)
		if entry.is_new():
			entry.insert()
		return entry.name
	frappe.throw(_("Stock preparation is available only for return pickup or transfer receipt PODs."))
