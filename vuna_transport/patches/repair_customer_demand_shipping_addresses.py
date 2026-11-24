import frappe

from vuna_transport.services.demand.shipping_address_repair import repair_shipping_address_snapshots


def execute():
	report = repair_shipping_address_snapshots(dry_run=False)
	if report["repaired"] or report["needs_review"]:
		print("Customer demand shipping-address repair: " + frappe.as_json(report))
