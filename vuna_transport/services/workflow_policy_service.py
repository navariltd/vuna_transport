"""Configurable dispatch workflow policies shared by UI and server operations."""

import frappe


ALLOCATION_BEFORE_DELIVERY_NOTES = "Before Delivery Notes"
ALLOCATION_AFTER_DELIVERY_NOTES = "After Delivery Notes"
VALID_ALLOCATION_TIMINGS = {
	ALLOCATION_BEFORE_DELIVERY_NOTES,
	ALLOCATION_AFTER_DELIVERY_NOTES,
}


def get_vehicle_allocation_timing():
	"""Return the configured sequence, defaulting to the new warehouse-first flow."""
	value = frappe.db.get_single_value(
		"Transport Settings", "vehicle_allocation_timing"
	)
	return (
		value
		if value in VALID_ALLOCATION_TIMINGS
		else ALLOCATION_AFTER_DELIVERY_NOTES
	)


def allocation_is_required_before_delivery_notes():
	return get_vehicle_allocation_timing() == ALLOCATION_BEFORE_DELIVERY_NOTES
