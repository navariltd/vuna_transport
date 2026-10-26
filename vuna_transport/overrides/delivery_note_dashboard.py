"""Extend Delivery Note connections with transport return cases."""

from frappe import _


def get_data(data):
	data["non_standard_fieldnames"]["Return Request"] = "return_stock_document"
	returns = next((row for row in data["transactions"] if row["label"] == _("Returns")), None)
	if returns:
		returns["items"].append("Return Request")
	else:
		data["transactions"].append({"label": _("Returns"), "items": ["Return Request"]})
	return data
