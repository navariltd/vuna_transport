# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from vuna_transport.services.mobile.mobile_device_service import bootstrap_device

# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]


class TestTransportMobileDeviceRegistration(UnitTestCase):
	def _new_device(self, values):
		device = frappe._dict(values)
		device.name = values["installation_id"]
		device.insert = lambda **kwargs: None
		return device

	@patch("vuna_transport.services.mobile.mobile_device_service._lock_mobile_user")
	@patch("vuna_transport.services.mobile.mobile_device_service._find_device", return_value=None)
	@patch("vuna_transport.services.mobile.mobile_device_service.frappe.get_doc")
	@patch("vuna_transport.services.mobile.mobile_device_service.frappe.db.get_value", return_value=None)
	@patch(
		"vuna_transport.services.mobile.mobile_device_service.now_datetime",
		return_value="2026-10-15 08:00:00",
	)
	def test_first_device_is_activated(self, _now_datetime, _get_value, get_doc, _find_device, _lock):
		get_doc.side_effect = self._new_device
		original_user = frappe.session.user
		try:
			frappe.session.user = "driver@example.com"
			result = bootstrap_device(installation_id="INSTALLATION-1", device_identifier="DEVICE-1")
		finally:
			frappe.session.user = original_user

		self.assertTrue(result["allowed"])
		self.assertEqual(result["device"]["status"], "Active")

	@patch("vuna_transport.services.mobile.mobile_device_service._lock_mobile_user")
	@patch("vuna_transport.services.mobile.mobile_device_service._find_device", return_value=None)
	@patch("vuna_transport.services.mobile.mobile_device_service.frappe.get_doc")
	@patch(
		"vuna_transport.services.mobile.mobile_device_service.frappe.db.get_value",
		return_value="EXISTING-DEVICE",
	)
	@patch(
		"vuna_transport.services.mobile.mobile_device_service.now_datetime",
		return_value="2026-10-15 08:00:00",
	)
	def test_second_device_requires_approval(self, _now_datetime, _get_value, get_doc, _find_device, _lock):
		get_doc.side_effect = self._new_device
		original_user = frappe.session.user
		try:
			frappe.session.user = "driver@example.com"
			result = bootstrap_device(installation_id="INSTALLATION-2", device_identifier="DEVICE-2")
		finally:
			frappe.session.user = original_user

		self.assertFalse(result["allowed"])
		self.assertEqual(result["device"]["status"], "Pending Approval")
		self.assertIn("awaiting administrator approval", result["message"])

	@patch("vuna_transport.services.mobile.mobile_device_service._lock_mobile_user")
	@patch("vuna_transport.services.mobile.mobile_device_service._find_device")
	@patch("vuna_transport.services.mobile.mobile_device_service.frappe.get_doc")
	@patch(
		"vuna_transport.services.mobile.mobile_device_service.frappe.db.get_value",
		return_value="OTHER-ACTIVE-DEVICE",
	)
	def test_legacy_duplicate_active_devices_are_rejected(self, _get_value, get_doc, find_device, _lock):
		device = frappe._dict(
			name="INSTALLATION-1",
			installation_id="INSTALLATION-1",
			latest_installation_id="INSTALLATION-1",
			device_identifier="DEVICE-1",
			platform="android",
			user="driver@example.com",
			status="Active",
		)
		find_device.return_value = device
		get_doc.return_value = device
		original_user = frappe.session.user
		try:
			frappe.session.user = "driver@example.com"
			result = bootstrap_device(installation_id="INSTALLATION-1", device_identifier="DEVICE-1")
		finally:
			frappe.session.user = original_user

		self.assertFalse(result["allowed"])
		self.assertIn("More than one active", result["message"])
