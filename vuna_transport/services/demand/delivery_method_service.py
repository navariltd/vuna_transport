"""Order applicability and transport responsibility, not direction or fleet sourcing."""

import frappe
from frappe import _

COMPANY_DELIVERY = "Company Delivery"


def canonical_delivery_method(delivery_method):
	return delivery_method or None


def delivery_methods_are_compatible(first, second):
	"""Only the same configured method is interchangeable for load planning."""
	return (canonical_delivery_method(first) or None) == (canonical_delivery_method(second) or None)


def get_delivery_method_workflow(delivery_method):
	"""Return workflow behavior without deriving transport resource sourcing."""
	name = canonical_delivery_method(delivery_method)
	if not name:
		return frappe._dict(
			name=None,
			disabled=False,
			requires_transport_planning=True,
			requires_vehicle_allocation=True,
			requires_delivery_trip=True,
		)
	method = frappe.get_cached_doc("Delivery Method", name)
	validate_method_configuration(method)
	return frappe._dict(
		name=method.name,
		disabled=bool(method.disabled),
		buying=bool(method.buying),
		selling=bool(method.selling),
		internal=bool(getattr(method, "internal", 0)),
		transport_arranged_by=method.transport_arranged_by,
		requires_transport_planning=bool(method.requires_transport_planning),
		requires_vehicle_allocation=bool(method.requires_vehicle_allocation),
		requires_delivery_trip=bool(method.requires_delivery_trip),
	)


def validate_delivery_method_allows_vehicle_allocation(delivery_method):
	"""Reject allocation for fulfilment methods that bypass company transport."""
	workflow = get_delivery_method_workflow(delivery_method)
	if workflow.disabled:
		frappe.throw(_("Delivery Method {0} is disabled.").format(workflow.name))
	if not workflow.requires_transport_planning or not workflow.requires_vehicle_allocation:
		frappe.throw(
			_("Delivery Method {0} does not permit Vehicle Allocation.").format(workflow.name or _("Not Set"))
		)
	return workflow


def get_purchase_delivery_method_workflow(name):
	if not name:
		return frappe._dict(
			name=None, transport_arranged_by=None, disabled=False, allowed=True, company_pickup=False
		)
	method = frappe.get_cached_doc("Delivery Method", name)
	validate_method_configuration(method)
	return frappe._dict(
		name=method.name,
		transport_arranged_by=method.transport_arranged_by,
		disabled=bool(method.disabled),
		allowed=bool(method.buying),
		company_pickup=bool(method.buying)
		and method.transport_arranged_by == "Company"
		and not method.disabled,
	)


def is_company_pickup_order(order):
	return get_purchase_delivery_method_workflow(order.get("vuna_delivery_method")).company_pickup


def validate_method_configuration(method):
	"""Responsibility is independent of labels, direction and fleet sourcing."""
	if not method.buying and not method.selling and not getattr(method, "internal", 0):
		frappe.throw(_("Select at least one of Buying, Selling or Internal."))
	if method.transport_arranged_by not in ("Company", "Counterparty"):
		frappe.throw(_("Select who arranges transport: Company or Counterparty."))
	planning = bool(method.requires_transport_planning)
	allocation = bool(method.requires_vehicle_allocation)
	trip = bool(method.requires_delivery_trip)
	if method.transport_arranged_by == "Counterparty" and (planning or allocation or trip):
		frappe.throw(
			_("Counterparty-arranged transport cannot require company planning, allocation or trips.")
		)
	if allocation and not planning:
		frappe.throw(_("Vehicle Allocation requires Transport Planning."))
	if trip and not allocation:
		frappe.throw(_("Delivery Trip requires Vehicle Allocation."))


def validate_sales_order_delivery_method(doc, method=None):
	"""Enforce order eligibility even for API/imported orders."""
	name = getattr(doc, "vuna_delivery_method", None)
	if not name:
		return
	workflow = get_delivery_method_workflow(name)
	if workflow.disabled or not workflow.selling:
		frappe.throw(_("Select an enabled Delivery Method with Selling enabled."))
	return workflow


def get_source_delivery_method_workflow(source, required=False):
	"""Transfer and return applicability is independent of custody and inventory."""
	is_return = source.doctype == "Return Request"
	if not is_return and (
		source.doctype != "Material Request" or source.get("material_request_type") != "Material Transfer"
	):
		return None
	fieldname = "delivery_method" if is_return else "vuna_delivery_method"
	name = source.get(fieldname)
	if not name:
		if required:
			frappe.throw(_("Select a Delivery Method before arranging transport."))
		return None
	workflow = get_delivery_method_workflow(name)
	internal = (
		not is_return or source.get("return_context") == "Depot" or bool(source.get("source_stock_entry"))
	)
	if workflow.disabled or not (workflow.internal if internal else workflow.selling):
		frappe.throw(
			_("Select an enabled Delivery Method with {0} enabled.").format(
				_("Internal") if internal else _("Selling")
			)
		)
	return workflow


def validate_source_delivery_method(doc, method=None):
	workflow = get_source_delivery_method_workflow(doc, required=doc.docstatus == 1)
	if workflow is None:
		return
	if doc.doctype == "Return Request":
		doc.transport_arranged_by = workflow.transport_arranged_by
	before_save = getattr(doc, "get_doc_before_save", None)
	previous = before_save() if callable(before_save) else None
	fieldname = "delivery_method" if doc.doctype == "Return Request" else "vuna_delivery_method"
	if not previous or previous.get(fieldname) == doc.get(fieldname):
		return
	if frappe.db.exists(
		"Transport Demand",
		{"source_document_type": doc.doctype, "source_document": doc.name, "docstatus": ["!=", 2]},
	) or (
		doc.doctype == "Return Request"
		and frappe.db.exists(
			"Transport Trip Task", {"return_request": doc.name, "status": ["!=", "Cancelled"]}
		)
	):
		frappe.throw(
			_(
				"Remove or cancel linked transport demands and pickup assignments before changing Delivery Method."
			)
		)
	return workflow


def source_uses_company_transport(source):
	workflow = get_source_delivery_method_workflow(source, required=True)
	return bool(workflow and workflow.transport_arranged_by == "Company")
