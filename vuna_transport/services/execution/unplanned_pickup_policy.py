"""Opt-in collection reporting; switching it off must not strand existing cargo."""

from collections import Counter

import frappe
from frappe import _
from frappe.utils import cint


def unplanned_pickups_enabled():
	return bool(cint(frappe.get_cached_doc("Transport Settings").get("allow_unplanned_return_pickups")))


def require_unplanned_pickups_enabled():
	if not unplanned_pickups_enabled():
		frappe.throw(_("Unplanned return pickups are disabled. Use an approved Return Request pickup task."))


@frappe.whitelist()
def get_unplanned_pickup_policy():
	from vuna_transport.services.shared.security_service import require_doctype_permission

	require_doctype_permission("Delivery Trip", "read")
	return {"enabled": unplanned_pickups_enabled()}


def attach_collection_visibility(trip_name, stops):
	enabled = unplanned_pickups_enabled()
	counts = Counter(
		frappe.get_all(
			"Proof of Delivery",
			filters={"delivery_trip": trip_name, "proof_type": "Unplanned Collection", "docstatus": 1},
			pluck="collection_stop",
		)
		if trip_name
		else []
	)
	for stop in stops:
		stop["unplanned_pickups_enabled"] = enabled
		stop["unplanned_collection_count"] = counts.get(stop.name, 0)
