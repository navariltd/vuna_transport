"""Bring existing Vehicle Allocation lifecycle states in line with their execution records."""

import frappe

from vuna_transport.services.fleet.vehicle_allocation_service import sync_allocation_execution_status


def execute():
	for allocation_name in frappe.get_all(
		"Vehicle Allocation",
		filters={"docstatus": 1, "status": ["not in", ("Replaced", "Cancelled")]},
		pluck="name",
	):
		sync_allocation_execution_status(allocation_name)
