"""Allowlisted source policies; configuration cannot introduce arbitrary adapters."""

import frappe
from frappe import _

SUPPORTED_SOURCES = ("Sales Order", "Material Request")


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_supported_source_query(
	doctype: str,
	txt: str,
	searchfield: str,
	start: int,
	page_len: int,
	filters: dict | str | None = None,
	as_dict: bool = False,
) -> list:
	"""Filter the DocType Link using the same allowlist as server validation."""
	from frappe.utils import cint

	from vuna_transport.services.shared.security_service import require_doctype_permission

	require_doctype_permission("Transport Settings", "read")
	names = [name for name in SUPPORTED_SOURCES if txt.lower() in name.lower()]
	offset = max(cint(start), 0)
	return [[name] for name in names[offset : offset + min(max(cint(page_len), 1), 50)]]


def get_source_policy(source_type):
	settings = frappe.get_cached_doc("Transport Settings")
	rows = settings.get("demand_sources") or []
	if rows:
		row = next((row for row in rows if row.source_document_type == source_type), None)
		return frappe._dict(
			enabled=bool(settings.enabled and row and row.enabled),
			creation_mode=row.creation_mode if row else "Manual",
			auto_submit_delivery_orders=bool(row and row.get("auto_submit_delivery_orders")),
			auto_submit_demand=bool(row and row.auto_submit_demand),
			required_workflow_state=row.required_workflow_state if row else None,
		)
	# Compatibility only for sites that have not run the source-settings migration.
	return frappe._dict(
		enabled=bool(settings.enabled and source_type in SUPPORTED_SOURCES),
		creation_mode="Automatic"
		if source_type == "Sales Order" and settings.get("auto_create_delivery_orders")
		else "Manual",
		auto_submit_delivery_orders=bool(settings.get("auto_submit_delivery_orders")),
		auto_submit_demand=False,
		required_workflow_state=None,
	)


def policy_source(source):
	return "Sales Order" if source.doctype == "Delivery Order" else source.doctype


def require_source_enabled(source):
	policy = get_source_policy(policy_source(source))
	if not policy.enabled:
		frappe.throw(_("Transport demand generation is disabled for this source in Transport Settings."))
	approval_source = (
		frappe.get_doc("Sales Order", source.sales_order) if source.doctype == "Delivery Order" else source
	)
	if (
		policy.required_workflow_state
		and approval_source.get("workflow_state") != policy.required_workflow_state
	):
		frappe.throw(_("The source has not reached the configured transport approval state."))
	return policy


def automatically_create_from_source(doc, method=None):
	policy = get_source_policy(policy_source(doc))
	if not policy.enabled or policy.creation_mode != "Automatic" or doc.docstatus != 1:
		return []
	approval_source = (
		frappe.get_doc("Sales Order", doc.sales_order) if doc.doctype == "Delivery Order" else doc
	)
	if (
		policy.required_workflow_state
		and approval_source.get("workflow_state") != policy.required_workflow_state
	):
		return []
	if doc.doctype == "Sales Order":
		from vuna_transport.services.demand.delivery_order_service import (
			create_delivery_orders_for_sales_order,
		)

		return create_delivery_orders_for_sales_order(doc)
	if doc.doctype == "Material Request" and doc.material_request_type != "Material Transfer":
		return []
	if doc.doctype not in ("Delivery Order", "Material Request"):
		return []
	from vuna_transport.services.demand.transport_demand_service import (
		_create_transport_demands,
		build_source_legs,
	)

	if doc.get("status") in ("Stopped", "Closed", "Cancelled") or not build_source_legs(doc):
		return []

	return _create_transport_demands(doc.doctype, doc.name, automated=True)


@frappe.whitelist()
def get_transport_source_policy(source_type: str) -> dict:
	from vuna_transport.services.shared.security_service import require_doctype_permission

	if source_type not in SUPPORTED_SOURCES:
		frappe.throw(_("This transport source is not supported."))
	require_doctype_permission(source_type, "read")
	return dict(get_source_policy(source_type))
