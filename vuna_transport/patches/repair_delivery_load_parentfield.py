"""Move existing Transport Load child rows to the renamed table field."""

import frappe


def execute():
	frappe.db.sql(
		"""
		update `tabTransport Load Item`
		set parentfield = 'delivery_orders'
		where parenttype = 'Transport Load'
		and parentfield = 'load_requirements'
		"""
	)
