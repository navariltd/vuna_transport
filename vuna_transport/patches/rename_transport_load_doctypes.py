"""Preserve load documents and link values while adopting transport terminology."""

import frappe


def execute():
	for old, new in (("Delivery Load Order", "Transport Load Item"), ("Delivery Load", "Transport Load")):
		if not frappe.db.exists("DocType", old):
			continue
		if frappe.db.exists("DocType", new):
			frappe.throw(f"Both {old} and {new} exist. Resolve the naming collision before migration.")
		frappe.rename_doc("DocType", old, new, force=True, show_alert=False, rebuild_search=False)
