"""Validate device event evidence without changing business posting rules."""

import json
from datetime import UTC, datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from vuna_transport.services.mobile import location_evidence as service


class TestMobileLocationEvidence(TestCase):
	def setUp(self):
		self.enterContext(patch.object(service, "get_system_timezone", return_value="Africa/Nairobi"))
		self.document = frappe._dict()
		self.captured = datetime.now(UTC) - timedelta(days=2)
		self.evidence = {
			"status": "Captured",
			"latitude": -1.2,
			"longitude": 36.8,
			"accuracy": 7.5,
			"captured_at": self.captured.isoformat(),
		}

	def apply(self):
		service.apply_device_location(self.document, {"device_location": self.evidence})

	def test_offline_evidence_retains_original_time_and_coordinates(self):
		self.apply()
		self.assertEqual(self.document.latitude, -1.2)
		self.assertEqual(self.document.longitude, 36.8)
		self.assertEqual(self.document.location_accuracy, 7.5)
		self.assertEqual(self.document.location_captured_at.hour, (self.captured.hour + 3) % 24)
		self.assertEqual(
			json.loads(self.document.location)["features"][0]["geometry"]["coordinates"], [36.8, -1.2]
		)

	def test_unavailable_fix_has_explicit_status_not_false_coordinates(self):
		self.apply()
		for status in ("Permission Denied", "Unavailable", "Timed Out"):
			service.apply_device_location(self.document, {"device_location": {"status": status}})
			self.assertEqual(self.document.location_capture_status, status)
			self.assertIsNone(self.document.latitude)
			self.assertIsNone(self.document.location)
			self.assertIsNone(self.document.location_captured_at)

	def test_coordinates_must_be_finite_and_in_range_with_nonnegative_accuracy(self):
		for field, value in (
			("latitude", 91),
			("longitude", -181),
			("latitude", "nan"),
			("longitude", "inf"),
			("accuracy", -1),
		):
			with self.subTest(field=field, value=value):
				original = self.evidence[field]
				self.evidence[field] = value
				with self.assertRaises(frappe.ValidationError):
					self.apply()
				self.evidence[field] = original

	def test_capture_time_requires_timezone_and_must_not_be_in_future(self):
		for timestamp in (
			"not-a-date",
			"2026-11-01 09:00:00",
			(datetime.now(UTC) + timedelta(days=1)).isoformat(),
		):
			self.evidence["captured_at"] = timestamp
			with self.assertRaises(frappe.ValidationError):
				self.apply()

	def test_missing_payload_preserves_existing_evidence_for_older_queued_operations(self):
		self.apply()
		before = dict(self.document)
		service.apply_device_location(self.document, {})
		self.assertEqual(self.document, before)

	def test_zero_coordinates_are_not_mistaken_for_missing_values(self):
		self.evidence.update(latitude=0, longitude=0, accuracy=0)
		self.apply()
		self.assertEqual(self.document.location_capture_status, "Captured")
		self.assertEqual(self.document.latitude, 0)

	def test_unavailable_payload_cannot_smuggle_a_location(self):
		self.evidence["status"] = "Unavailable"
		with self.assertRaises(frappe.ValidationError):
			self.apply()

	def test_malformed_evidence_is_a_validation_error_not_a_server_error(self):
		for evidence in (
			[],
			{"status": []},
			{**self.evidence, "captured_at": 123},
			{**self.evidence, "latitude": True},
		):
			with self.subTest(evidence=evidence), self.assertRaises(frappe.ValidationError):
				service.apply_device_location(self.document, {"device_location": evidence})

	def test_incident_endpoint_saves_device_evidence_before_insert(self):
		from vuna_transport.services.mobile import mobile_trip_execution_service as incidents

		trip = frappe._dict(name="TRIP", company="Company", vehicle="Vehicle")
		driver = frappe._dict(name="DRIVER", full_name="Driver")
		incident = frappe._dict(name="INCIDENT", insert=Mock(), add_comment=Mock())
		with (
			patch.object(incidents, "_lock_assigned_trip", return_value=(driver, trip)),
			patch.object(incidents, "now_datetime", return_value=datetime.now(UTC)),
			patch.object(incidents, "_require_started_trip"),
			patch.object(incidents, "get_delivery_trip_load_names", return_value=[]),
			patch.object(incidents.frappe.db, "get_value", return_value=None),
			patch.object(incidents.frappe, "get_doc", return_value=incident),
		):
			incidents.report_mobile_trip_incident(
				"INSTALLATION",
				"TRIP",
				{
					"request_id": "52f15a24-fd9c-45b7-b395-269353b5ad79",
					"incident_type": incidents.INCIDENT_TYPES[0],
					"description": "Vehicle stopped",
					"device_location": self.evidence,
				},
			)
		self.assertEqual((incident.latitude, incident.longitude), (-1.2, 36.8))
		self.assertEqual(incident.location_capture_status, "Captured")
		incident.insert.assert_called_once_with(ignore_permissions=True)
