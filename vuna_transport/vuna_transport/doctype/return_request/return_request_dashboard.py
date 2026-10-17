# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

from frappe import _


def get_data():
	return {
		"internal_links": {
			"Proof of Delivery": "proof_of_delivery",
			"Delivery Trip": "delivery_trip",
			"Delivery Note": "return_stock_document",
			"Quality Inspection": ["items", "quality_inspection"],
			"Sales Invoice": "credit_note",
			"Sales Order": "replacement_sales_order",
			"Payment Entry": "refund_payment_entry",
			"Delivery Order": "replacement_delivery_reference",
		},
		"transactions": [
			{
				"label": _("Delivery Context"),
				"items": ["Proof of Delivery", "Delivery Trip"],
			},
			{
				"label": _("Physical Return"),
				"items": ["Delivery Note", "Quality Inspection"],
			},
			{
				"label": _("Commercial Resolution"),
				"items": ["Sales Invoice", "Sales Order", "Payment Entry", "Delivery Order"],
			},
		],
	}
