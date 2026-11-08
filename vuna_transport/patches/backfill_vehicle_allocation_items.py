"""Populate the read-only item mirror for existing submitted allocations."""

import frappe

from vuna_transport.services.fleet.allocation_cargo_service import collect_load_items
from vuna_transport.services.fleet.vehicle_allocation_service import get_allocation_load_names


def execute():
	result = {"updated": 0, "skipped": []}
	for name in frappe.get_all("Vehicle Allocation", filters={"docstatus": 1}, pluck="name"):
		if frappe.db.exists("Vehicle Allocation Item", {"parent": name, "parenttype": "Vehicle Allocation"}):
			continue
		allocation = frappe.get_doc("Vehicle Allocation", name)
		loads = [frappe.get_doc("Transport Load", load) for load in get_allocation_load_names(allocation)]
		if any(load.docstatus == 2 for load in loads):
			result["skipped"].append(name)
			continue
		try:
			items = collect_load_items(loads)
		except (frappe.ValidationError, frappe.DoesNotExistError):
			result["skipped"].append(name)
			continue
		for index, values in enumerate(items, start=1):
			frappe.get_doc(
				{
					"doctype": "Vehicle Allocation Item",
					"parent": name,
					"parenttype": "Vehicle Allocation",
					"parentfield": "loaded_items",
					"docstatus": 1,
					"idx": index,
					**values,
				}
			).db_insert()
		result["updated"] += 1
	return result
