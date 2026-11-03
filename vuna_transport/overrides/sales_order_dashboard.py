"""Extend the Sales Order Connections tab with transport demand links."""

from frappe import _


def get_data(data):
	data["non_standard_fieldnames"].update(
		{
			"Delivery Order": "sales_order",
			"Transport Load": "sales_order",
		}
	)
	data["transactions"].append(
		{
			"label": _("Transport Fulfillment"),
			"items": ["Delivery Order", "Transport Load"],
		}
	)
	return data
