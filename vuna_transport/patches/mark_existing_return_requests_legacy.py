"""Keep pre-V2 Return Requests on their original workflow."""

import frappe


def execute():
	if not frappe.db.table_exists("Return Request") or not frappe.db.has_column(
		"Return Request", "process_version"
	):
		return
	frappe.db.sql(
		"""
		update `tabReturn Request`
		set process_version = 'Legacy'
		where coalesce(process_version, '') != 'Legacy'
		"""
	)
