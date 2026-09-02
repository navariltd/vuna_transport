"""Helpers for normalising transport measurements into configured UOMs."""

import frappe
from erpnext.stock.doctype.item.item import get_uom_conv_factor
from frappe import _
from frappe.utils import flt


def get_transport_uoms():
	settings = frappe.get_cached_doc("Transport Settings")
	return frappe._dict(
		weight_uom=settings.default_weight_uom,
		volume_uom=settings.default_volume_uom,
		distance_uom=settings.default_distance_uom,
	)


def convert_measurement(value, from_uom, to_uom, label):
	"""Convert a physical measurement using ERPNext's global UOM conversion table."""
	value = flt(value)
	if not value or not to_uom:
		return value
	if not from_uom:
		return value
	if from_uom == to_uom:
		return value

	factor = get_uom_conv_factor(from_uom, to_uom)
	if factor is None:
		frappe.throw(
			_("Add a UOM Conversion Factor from {0} to {1} before calculating {2}.").format(
				from_uom, to_uom, label
			)
		)
	return value * flt(factor)
