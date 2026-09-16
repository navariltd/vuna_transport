"""Move fuel efficiency distance UOM to ERPNext Vehicle.uom and remove the duplicate field."""

import frappe


def execute():
	if frappe.db.has_column("Vehicle", "vuna_fuel_efficiency_uom") and frappe.db.has_column("Vehicle", "uom"):
		frappe.db.sql(
			"""
			update `tabVehicle`
			set uom = vuna_fuel_efficiency_uom
			where (uom is null or uom = '')
			and vuna_fuel_efficiency_uom is not null
			and vuna_fuel_efficiency_uom != ''
			"""
		)
	if frappe.db.exists("Custom Field", "Vehicle-vuna_fuel_efficiency_uom"):
		frappe.delete_doc("Custom Field", "Vehicle-vuna_fuel_efficiency_uom", ignore_missing=True)
