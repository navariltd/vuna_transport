"""Fill the new constituency snapshot without changing existing geography history."""

import frappe

from vuna_transport.services.routing.location_service import get_address_planning_data


def execute():
	if not frappe.db.exists("DocType", "Delivery Order"):
		return
	if not frappe.db.has_column("Delivery Order", "constituency"):
		return

	for row in frappe.get_all(
		"Delivery Order",
		filters={"constituency": ["is", "not set"]},
		fields=["name", "delivery_address"],
		limit_page_length=0,
	):
		constituency = get_address_planning_data(row.delivery_address).get("constituency")
		if constituency:
			frappe.db.set_value(
				"Delivery Order",
				row.name,
				"constituency",
				constituency,
				update_modified=False,
			)
