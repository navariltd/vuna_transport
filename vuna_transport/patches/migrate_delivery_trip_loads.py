"""Move legacy Delivery Trip load links onto the Delivery Stop rows."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import delete_custom_fields


def execute():
	legacy_global_field = frappe.db.has_column("Delivery Trip", "vuna_delivery_load")
	trip_fields = ["name"]
	if legacy_global_field:
		trip_fields.append("vuna_delivery_load")
	for fieldname in ("vuna_vehicle_allocation", "vuna_current_vehicle_allocation"):
		if frappe.db.has_column("Delivery Trip", fieldname):
			trip_fields.append(fieldname)

	trip_filters = {"vuna_delivery_load": ["is", "set"]} if legacy_global_field else {}
	trips = frappe.db.get_all("Delivery Trip", filters=trip_filters, fields=trip_fields)
	child_loads = {}
	if frappe.db.table_exists("Delivery Trip Load"):
		for row in frappe.db.sql(
			"select parent, delivery_load from `tabDelivery Trip Load` where ifnull(delivery_load, '') != ''",
			as_dict=True,
		):
			child_loads.setdefault(row.parent, []).append(row.delivery_load)

	for trip in trips:
		load_names = list(
			dict.fromkeys(
				([trip.vuna_delivery_load] if trip.get("vuna_delivery_load") else [])
				+ child_loads.get(trip.name, [])
			)
		)
		if not load_names:
			continue
		for stop in frappe.db.get_all(
			"Delivery Stop",
			filters={"parent": trip.name, "parenttype": "Delivery Trip"},
			fields=["name", "delivery_note", "vuna_delivery_load"],
		):
			load_name = stop.vuna_delivery_load
			if not load_name and stop.delivery_note:
				load_name = frappe.db.get_value("Delivery Note", stop.delivery_note, "vuna_delivery_load")
			if not load_name and len(load_names) == 1:
				load_name = load_names[0]
			if load_name in load_names and load_name != stop.vuna_delivery_load:
				frappe.db.set_value(
					"Delivery Stop", stop.name, "vuna_delivery_load", load_name, update_modified=False
				)

	delete_custom_fields({"Delivery Trip": ["vuna_delivery_load"]}, bypass_hooks=True)
