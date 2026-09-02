"""Installation helpers for Vuna Transport."""

import frappe


DEFAULT_TRANSPORT_UOMS = {
	"default_weight_uom": "Kg",
	"default_volume_uom": "Cubic Meter",
	"default_distance_uom": "Kilometer",
}


def after_install():
	ensure_transport_uoms()


def ensure_transport_uoms():
	"""Ensure transport defaults exist after ERPNext masters are initialized."""
	for uom_name in DEFAULT_TRANSPORT_UOMS.values():
		if not frappe.db.exists("UOM", uom_name):
			frappe.get_doc(
				{
					"doctype": "UOM",
					"uom_name": uom_name,
					"enabled": 1,
				}
			).insert(ignore_permissions=True)

	for fieldname, uom_name in DEFAULT_TRANSPORT_UOMS.items():
		if not frappe.db.get_single_value("Transport Settings", fieldname):
			frappe.db.set_single_value("Transport Settings", fieldname, uom_name)

	frappe.db.commit()
