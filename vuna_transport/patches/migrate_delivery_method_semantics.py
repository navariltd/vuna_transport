"""Normalize Delivery Method as business fulfilment, not capacity sourcing."""

import frappe
from frappe.model.utils.rename_field import rename_field


COMPANY_DELIVERY = "Company Delivery"
LEGACY_METHODS = ("Company Fleet", "Subcontracted Transport")
REFERENCE_FIELDS = (
	("Sales Order", "vuna_delivery_method"),
	("Delivery Order", "delivery_method"),
	("Delivery Load", "delivery_method"),
)
FULFILMENT_TYPE_MAP = {
	"Internal Fleet": COMPANY_DELIVERY,
	"Transporter": COMPANY_DELIVERY,
	"Customer": "Customer Pickup",
	"Courier": "Courier",
	"Supplier": "Direct Supplier Delivery",
	"Other": "Other",
}


def execute():
	if frappe.db.has_column("Delivery Method", "delivery_provider_type"):
		rename_field(
			"Delivery Method", "delivery_provider_type", "fulfilment_type"
		)

	_normalize_fulfilment_types()
	_ensure_company_delivery()
	for legacy_method in LEGACY_METHODS:
		_update_references(legacy_method, COMPANY_DELIVERY)
		if frappe.db.exists("Delivery Method", legacy_method):
			frappe.db.set_value(
				"Delivery Method",
				legacy_method,
				{"disabled": 1, "fulfilment_type": COMPANY_DELIVERY},
				update_modified=False,
			)
	_normalize_customer_pickup()


def _normalize_fulfilment_types():
	for method in frappe.get_all(
		"Delivery Method", fields=["name", "fulfilment_type"]
	):
		normalized = FULFILMENT_TYPE_MAP.get(method.fulfilment_type)
		if normalized and normalized != method.fulfilment_type:
			frappe.db.set_value(
				"Delivery Method",
				method.name,
				"fulfilment_type",
				normalized,
				update_modified=False,
			)


def _ensure_company_delivery():
	if not frappe.db.exists("Delivery Method", COMPANY_DELIVERY):
		doc = frappe.new_doc("Delivery Method")
		doc.delivery_method_name = COMPANY_DELIVERY
		doc.fulfilment_type = COMPANY_DELIVERY
		doc.requires_transport_planning = 1
		doc.requires_vehicle_allocation = 1
		doc.requires_delivery_trip = 1
		doc.insert(ignore_permissions=True)
	else:
		frappe.db.set_value(
			"Delivery Method",
			COMPANY_DELIVERY,
			{
				"fulfilment_type": COMPANY_DELIVERY,
				"requires_transport_planning": 1,
				"requires_vehicle_allocation": 1,
				"requires_delivery_trip": 1,
				"disabled": 0,
			},
			update_modified=False,
		)


def _update_references(old_value, new_value):
	for doctype, fieldname in REFERENCE_FIELDS:
		if frappe.db.has_column(doctype, fieldname):
			frappe.db.set_value(
				doctype,
				{fieldname: old_value},
				fieldname,
				new_value,
				update_modified=False,
			)


def _normalize_customer_pickup():
	if not frappe.db.exists("Delivery Method", "Customer Pickup"):
		return
	frappe.db.set_value(
		"Delivery Method",
		"Customer Pickup",
		{
			"fulfilment_type": "Customer Pickup",
			"requires_transport_planning": 0,
			"requires_vehicle_allocation": 0,
			"requires_delivery_trip": 0,
		},
		update_modified=False,
	)
