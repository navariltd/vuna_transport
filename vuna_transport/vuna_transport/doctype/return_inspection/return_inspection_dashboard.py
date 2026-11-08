from frappe import _


def get_data():
	return {
		"internal_links": {
			"Delivery Note": "sales_return",
			"Return Request": "return_request",
			"Stock Entry": "stock_entry",
			"Quality Inspection": ["results", "quality_inspection"],
		},
		"transactions": [
			{"label": _("Return Case"), "items": ["Return Request", "Delivery Note"]},
			{"label": _("Quality & Disposition"), "items": ["Quality Inspection", "Stock Entry"]},
		],
	}
