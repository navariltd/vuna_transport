"""Preserve custom collection locations in ERPNext Dispatch Address before removal."""

import frappe
from frappe import _
from frappe.contacts.doctype.address.address import get_address_display


def execute():
	field = frappe.db.get_value(
		"Custom Field", {"dt": "Purchase Order", "fieldname": "vuna_pickup_address"}, "name"
	)
	if not field:
		return  # Already removed, or never installed. Do not replay retained raw data.
	rows = []
	if frappe.db.has_column("Purchase Order", "vuna_pickup_address"):
		rows = frappe.db.sql(
			"""select name, vuna_pickup_address, dispatch_address
			from `tabPurchase Order` where coalesce(vuna_pickup_address,'')!=''
			order by name for update""",
			as_dict=True,
		)
	conflicts = [
		row.name for row in rows if row.dispatch_address and row.dispatch_address != row.vuna_pickup_address
	]
	if conflicts:
		frappe.throw(
			_(
				"Resolve differing Dispatch Address and legacy Supplier Pickup Address on Purchase Orders before retrying: {0}"
			).format(", ".join(conflicts))
		)
	# Build every display first, so missing/invalid address records stop before
	# any PO updates. Never submit/amend orders or change transport snapshots.
	updates = [
		(row.name, row.vuna_pickup_address, get_address_display(row.vuna_pickup_address))
		for row in rows
		if not row.dispatch_address
	]
	for name, address, display in updates:
		frappe.db.set_value(
			"Purchase Order",
			name,
			{
				"dispatch_address": address,
				"dispatch_address_display": display,
			},
			update_modified=False,
		)
	frappe.delete_doc("Custom Field", field, ignore_permissions=True)
	# Frappe retains the old database column as recovery data, not an active field.
	frappe.clear_cache(doctype="Purchase Order")
