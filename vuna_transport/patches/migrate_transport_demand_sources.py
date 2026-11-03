"""Preserve customer delivery behaviour and default transfers to manual generation."""

import frappe


def execute():
	settings = frappe.get_single("Transport Settings")
	if settings.get("demand_sources"):
		return
	settings.append(
		"demand_sources",
		{
			"source_document_type": "Sales Order",
			"enabled": 1,
			"creation_mode": "Automatic" if settings.auto_create_delivery_orders else "Manual",
			"auto_submit_delivery_orders": settings.auto_submit_delivery_orders,
			"auto_submit_demand": 0,
		},
	)
	settings.append(
		"demand_sources",
		{
			"source_document_type": "Material Request",
			"enabled": 1,
			"creation_mode": "Manual",
			"auto_submit_demand": 0,
		},
	)
	settings.save(ignore_permissions=True)
