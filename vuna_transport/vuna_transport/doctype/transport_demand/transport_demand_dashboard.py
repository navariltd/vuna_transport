from frappe import _


def get_data():
	# Frappe resolves this external field through Transport Load Item, retaining
	# every native load rather than relying on a single parent header backlink.
	return {
		"fieldname": "transport_demand",
		"transactions": [{"label": _("Planning"), "items": ["Transport Load"]}],
	}
