from frappe import _


def get_data():
	return {
		"internal_links": {
			"Return Request": "return_request",
			"Proof of Delivery": "proof_of_delivery",
			"Delivery Trip": "delivery_trip",
			"Delivery Note": "sales_return",
			"Sales Order": "sales_order",
			"Stock Entry": "stock_entry",
			"Transport Trip Task": "execution_task",
		},
		"transactions": [
			{
				"label": _("Original Shipment"),
				"items": ["Return Request", "Proof of Delivery", "Delivery Trip", "Delivery Note"],
			},
			{"label": _("Onward Documents"), "items": ["Sales Order", "Stock Entry", "Transport Trip Task"]},
		],
	}
