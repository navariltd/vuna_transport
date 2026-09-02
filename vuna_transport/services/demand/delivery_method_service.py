"""Business-fulfilment semantics for Delivery Method.

Transport resource sourcing belongs to Vehicle Allocation.  The legacy aliases
remain here only so documents created before the terminology correction compare
and migrate safely.
"""

import frappe
from frappe import _

COMPANY_DELIVERY = "Company Delivery"
LEGACY_DELIVERY_METHOD_ALIASES = {
	"Company Fleet": COMPANY_DELIVERY,
	"Subcontracted Transport": COMPANY_DELIVERY,
}


def canonical_delivery_method(delivery_method):
	return LEGACY_DELIVERY_METHOD_ALIASES.get(delivery_method, delivery_method)


def delivery_methods_are_compatible(first, second):
	"""Compare business fulfilment methods, normalizing legacy sourcing labels."""
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
	return frappe._dict(
		name=method.name,
		disabled=bool(method.disabled),
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
