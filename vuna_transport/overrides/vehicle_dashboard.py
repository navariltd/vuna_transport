"""Extend the standard ERPNext Vehicle dashboard with transport records."""

from frappe import _


def get_data(data):
	data["non_standard_fieldnames"].update(
		{
			"Vehicle Document": "vehicle",
			"Vehicle Availability Block": "vehicle",
			"Vehicle Allocation": "vehicle",
			"Vehicle Incident": "vehicle",
		}
	)
	data["transactions"].append(
		{
			"label": _("Transport"),
			"items": [
				"Vehicle Document",
				"Vehicle Availability Block",
				"Vehicle Allocation",
				"Vehicle Incident",
			],
		}
	)
	return data
