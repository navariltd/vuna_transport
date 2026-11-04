from frappe import _


def get_data():
	return {
		"fieldname": "vuna_transport_task",
		"non_standard_fieldnames": {"Proof of Delivery": "transport_trip_task"},
		"transactions": [{"label": _("Stock Execution"), "items": ["Stock Entry", "Proof of Delivery"]}],
	}
