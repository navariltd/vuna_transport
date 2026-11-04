from frappe import _


def get_data():
	return {
		"fieldname": "proof_of_delivery",
		"non_standard_fieldnames": {"Stock Entry": "vuna_transport_proof"},
		"transactions": [{"label": _("Follow-up Documents"), "items": ["Return Request", "Stock Entry"]}],
	}
