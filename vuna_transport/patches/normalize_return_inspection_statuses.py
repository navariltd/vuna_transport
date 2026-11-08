"""Normalize Return Inspection status values to the document lifecycle."""

import frappe


def execute() -> None:
	frappe.db.sql(
		"""
		update `tabReturn Inspection`
		set status = 'Submitted'
		where status in ('Inspected', 'Disposed')
		"""
	)
