"""Allowlisted source policies; configuration cannot introduce arbitrary adapters."""

import frappe
from frappe import _

SUPPORTED_SOURCES = ("Sales Order", "Material Request", "Return Request", "Purchase Order")


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
		enabled=bool(settings.enabled and source_type in ("Sales Order", "Material Request")),
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
	if source.doctype == "Sales Order" and source.get("vuna_shipment_diversion"):
		frappe.throw(
			_(
				"This Sales Order is already assigned to a Shipment Diversion. Prepare its onward delivery from that diversion."
			)
		)
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
	if source.doctype in ("Material Request", "Return Request"):
		from vuna_transport.services.demand.delivery_method_service import source_uses_company_transport

		if not source_uses_company_transport(source):
			frappe.throw(_("The selected Delivery Method does not use company transport."))
	return policy


def automatically_create_from_source(doc, method=None):
	# Already approved and physically on a vehicle; do not plan its onward sale twice.
	if doc.doctype == "Sales Order" and doc.get("vuna_shipment_diversion"):
		from vuna_transport.services.execution.shipment_diversion_documents import validate_diversion_document

		validate_diversion_document(doc)
		return []
	policy = get_source_policy(policy_source(doc))
	if not policy.enabled or policy.creation_mode != "Automatic" or doc.docstatus != 1:
		return []
	if doc.get("status") in ("Stopped", "Closed", "Cancelled", "On Hold", "Completed"):
		return []
	if doc.doctype in ("Material Request", "Return Request"):
		from vuna_transport.services.demand.delivery_method_service import get_source_delivery_method_workflow

		workflow = get_source_delivery_method_workflow(doc)
		if (
			not workflow
			or workflow.transport_arranged_by != "Company"
			or not workflow.requires_transport_planning
		):
			return []
	method_name = (
		doc.get("delivery_method") if doc.doctype == "Delivery Order" else doc.get("vuna_delivery_method")
	)
	if doc.doctype in ("Sales Order", "Purchase Order", "Delivery Order") and method_name:
		from vuna_transport.services.demand.delivery_method_service import get_delivery_method_workflow

		if not get_delivery_method_workflow(method_name).requires_transport_planning:
			return []  # Manual transport is explicit, even when the source policy is automatic.
	approval_source = (
		frappe.get_doc("Sales Order", doc.sales_order) if doc.doctype == "Delivery Order" else doc
	)
	if (
		policy.required_workflow_state
		and approval_source.get("workflow_state") != policy.required_workflow_state
	):
		return []
	if doc.doctype == "Sales Order":
		from vuna_transport.services.demand.sales_transport_demand_service import (
			create_sales_demands,
			native_customer_demand_enabled,
		)

		if native_customer_demand_enabled():
			return create_sales_demands(doc, automated=True)
		from vuna_transport.services.demand.delivery_order_service import (
			create_delivery_orders_for_sales_order,
		)

		return create_delivery_orders_for_sales_order(doc)
	if doc.doctype == "Material Request" and doc.material_request_type != "Material Transfer":
		return []
	if doc.doctype == "Return Request" and doc.get("return_context") != "Depot":
		return []
	if doc.doctype == "Purchase Order":
		from vuna_transport.services.demand.delivery_method_service import is_company_pickup_order

		if not is_company_pickup_order(doc):
			return []
	if doc.doctype not in ("Delivery Order", "Material Request", "Return Request", "Purchase Order"):
		return []
	from vuna_transport.services.demand.transport_demand_service import (
		_create_transport_demands,
		build_source_legs,
	)

	if doc.get("status") in (
		"Stopped",
		"Closed",
		"Cancelled",
		"On Hold",
		"Completed",
	) or not build_source_legs(doc):
		return []

	return _create_transport_demands(doc.doctype, doc.name, automated=True)


@frappe.whitelist()
def get_transport_source_policy(
	source_type: str, delivery_method: str | None = None, return_context: str | None = None
) -> dict:
	from vuna_transport.services.shared.security_service import require_doctype_permission

	if source_type not in SUPPORTED_SOURCES:
		frappe.throw(_("This transport source is not supported."))
	require_doctype_permission(source_type, "read")
	result = dict(get_source_policy(source_type))
	if source_type == "Purchase Order":
		from vuna_transport.services.demand.delivery_method_service import (
			get_purchase_delivery_method_workflow,
		)

		result["company_pickup"] = get_purchase_delivery_method_workflow(delivery_method).company_pickup
	if source_type in ("Material Request", "Return Request"):
		from vuna_transport.services.demand.delivery_method_service import get_source_delivery_method_workflow

		workflow = get_source_delivery_method_workflow(
			frappe._dict(
				doctype=source_type,
				material_request_type="Material Transfer",
				vuna_delivery_method=delivery_method,
				delivery_method=delivery_method,
				return_context=return_context,
			)
		)
		result["company_transport"] = bool(workflow and workflow.transport_arranged_by == "Company")
	return result
