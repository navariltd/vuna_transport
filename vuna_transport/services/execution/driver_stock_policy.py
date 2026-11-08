"""Independent posting permissions for narrowly scoped transport proofs."""

import frappe
from frappe.utils import cint


def driver_purchase_receipts_enabled():
	return bool(cint(frappe.get_cached_doc("Transport Settings").get("allow_driver_purchase_receipt")))


def driver_end_transit_enabled():
	value = frappe.get_cached_doc("Transport Settings").get("allow_driver_end_transit")
	return value is None or bool(cint(value))


def initialize_driver_stock_policy():
	"""Seed missing singleton values without overriding an administrator's choice."""
	values = frappe.db.get_singles_dict("Transport Settings")
	for field, default in (("allow_driver_purchase_receipt", 0), ("allow_driver_end_transit", 1)):
		if field not in values:
			frappe.db.set_single_value("Transport Settings", field, default)
	frappe.clear_cache(doctype="Transport Settings")
