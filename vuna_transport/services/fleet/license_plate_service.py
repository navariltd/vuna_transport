"""Kenyan vehicle registration mark normalization and validation."""

import re

import frappe
from frappe import _


MOTOR_VEHICLE = "Motor Vehicle"
MOTORCYCLE = "Motorcycle"

_PLATE_FORMATS = {
	MOTOR_VEHICLE: {
		"pattern": re.compile(r"^K[A-Z]{2}\d{3}[A-Z]$"),
		"example": "KAA 123A",
		"split_at": 3,
	},
	MOTORCYCLE: {
		"pattern": re.compile(r"^K[A-Z]{3}\d{3}[A-Z]$"),
		"example": "KMCE 123A",
		"split_at": 4,
	},
}


def normalize_license_plate(value):
	"""Return a comparison-safe registration mark without display separators."""
	return re.sub(r"[\s-]+", "", (value or "").upper())


def get_license_plate_format(vehicle_category=None):
	"""Get the format configured on a Vehicle Category, defaulting to motor vehicles."""
	if not vehicle_category:
		return MOTOR_VEHICLE
	return (
		frappe.db.get_value("Vehicle Category", vehicle_category, "license_plate_format")
		or MOTOR_VEHICLE
	)


def format_license_plate(value, license_plate_format=MOTOR_VEHICLE):
	"""Validate and render a Kenyan registration mark in its canonical display form."""
	plate = normalize_license_plate(value)
	format_definition = _PLATE_FORMATS.get(license_plate_format)
	if not format_definition:
		frappe.throw(_("Unsupported License Plate Format {0}.").format(license_plate_format))
	if not format_definition["pattern"].fullmatch(plate):
		frappe.throw(
			_("License Plate must use the {0} format, for example {1}.").format(
				license_plate_format, format_definition["example"]
			)
		)
	return f"{plate[:format_definition['split_at']]} {plate[format_definition['split_at'] :]}"


def normalize_and_validate_license_plate(value, vehicle_category=None):
	"""Apply the Vehicle Category's registration-mark format to a value."""
	return format_license_plate(value, get_license_plate_format(vehicle_category))
