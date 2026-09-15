"""Move legacy Vehicle Allocation load links into the canonical child table."""

import frappe


def execute():
	if not frappe.db.table_exists("Vehicle Allocation Load"):
		return
	rows = frappe.db.sql(
		"""
			select name, delivery_load
			from `tabVehicle Allocation`
			where ifnull(delivery_load, '') != ''
		""",
		as_dict=True,
	)
	for row in rows:
		if frappe.db.exists(
			"Vehicle Allocation Load",
			{"parent": row.name, "parenttype": "Vehicle Allocation", "delivery_load": row.delivery_load},
		):
			continue
		frappe.get_doc(
			{
				"doctype": "Vehicle Allocation Load",
				"parent": row.name,
				"parenttype": "Vehicle Allocation",
				"parentfield": "delivery_loads",
				"delivery_load": row.delivery_load,
			}
		).insert(ignore_permissions=True)
