"""Populate missing location snapshots on existing development Delivery Orders."""

import frappe

from vuna_transport.services.routing.location_service import set_delivery_order_location_snapshots

SNAPSHOT_FIELDS = (
	"transport_zone",
	"territory",
	"county",
	"constituency",
	"town",
	"destination_location",
	"destination_latitude",
	"destination_longitude",
	"source_address",
	"source_location",
	"source_latitude",
	"source_longitude",
)


def execute():
	if not frappe.db.exists("DocType", "Delivery Order"):
		return
	for name in frappe.get_all("Delivery Order", pluck="name"):
		delivery_order = frappe.get_doc("Delivery Order", name)
		set_delivery_order_location_snapshots(delivery_order)
		values = {
			fieldname: delivery_order.get(fieldname)
			for fieldname in SNAPSHOT_FIELDS
			if delivery_order.get(fieldname) is not None
		}
		if values:
			frappe.db.set_value(
				"Delivery Order",
				name,
				values,
				update_modified=False,
			)
