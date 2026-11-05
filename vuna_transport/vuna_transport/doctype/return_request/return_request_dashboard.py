# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

from frappe import _


def get_data():
	return {
		# Enables Frappe's Connections client to fetch badge counts and resolved
		# document names. Sales Returns carry this reverse link on Delivery Note.
		"fieldname": "vuna_return_request",
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
		"non_standard_fieldnames": {
			"Transport Trip Task": "return_request",
			"Transport Demand": "source_document",
			"Return Inspection": "return_request",
		},
		"transactions": [
			{
				"label": _("Delivery Context"),
				"items": ["Proof of Delivery", "Delivery Trip", "Transport Trip Task"],
			},
			{
				"label": _("Physical Return"),
				"items": [
					"Delivery Note",
					"Stock Entry",
					"Return Inspection",
					"Quality Inspection",
					"Transport Demand",
				],
			},
			{
				"label": _("Commercial Resolution"),
				"items": ["Sales Invoice", "Sales Order", "Payment Entry", "Delivery Order"],
			},
		],
	}
