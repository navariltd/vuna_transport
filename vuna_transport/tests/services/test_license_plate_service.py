from unittest.mock import patch

from frappe.exceptions import ValidationError
from frappe.tests import UnitTestCase

from vuna_transport.services.fleet.license_plate_service import (
	MOTOR_VEHICLE,
	MOTORCYCLE,
	format_license_plate,
	normalize_and_validate_license_plate,
)


class TestLicensePlateService(UnitTestCase):
	def test_standard_vehicle_plate_is_normalized(self):
		self.assertEqual(format_license_plate("kaa-123a", MOTOR_VEHICLE), "KAA 123A")

	def test_motorcycle_plate_is_normalized(self):
		self.assertEqual(format_license_plate("kmce031z", MOTORCYCLE), "KMCE 031Z")

	def test_incorrect_format_is_rejected(self):
		with self.assertRaises(ValidationError):
			format_license_plate("KAA 123A", MOTORCYCLE)

	@patch("vuna_transport.services.fleet.license_plate_service.frappe.db.get_value")
	def test_category_format_is_used(self, get_value):
		get_value.return_value = MOTORCYCLE
		self.assertEqual(
			normalize_and_validate_license_plate("kmce 031z", "Motorcycle Category"), "KMCE 031Z"
		)
		get_value.assert_called_once_with("Vehicle Category", "Motorcycle Category", "license_plate_format")
