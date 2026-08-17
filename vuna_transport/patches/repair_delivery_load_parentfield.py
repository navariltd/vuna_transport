"""Move existing Delivery Load child rows to the renamed table field."""

import frappe


def execute():
	frappe.db.sql(
		"""
		update `tabDelivery Load Order`
		set parentfield = 'delivery_orders'
		where parenttype = 'Delivery Load'
		and parentfield = 'load_requirements'
		"""
	)
