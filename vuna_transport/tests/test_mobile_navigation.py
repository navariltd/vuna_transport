"""Navigation authorizes selected stops without changing transport documents."""

import unittest
from unittest.mock import patch
from uuid import uuid4

import frappe
from redis.exceptions import RedisError

from vuna_transport.services.mobile import mobile_navigation_service as navigation


class TestMobileNavigation(unittest.TestCase):
	def setUp(self):
		self.enterContext(patch.object(frappe, "throw", side_effect=ValueError))
		self.enterContext(patch.object(navigation, "_", side_effect=lambda value: value))
		self.driver = self.enterContext(
			patch.object(navigation, "_require_mobile_driver", return_value=frappe._dict(name="DRIVER"))
		)
		self.trip = frappe._dict(name="TRIP", delivery_stops=[frappe._dict(name="STOP", address="ADDRESS")])
		self.assigned = self.enterContext(
			patch.object(navigation, "_get_assigned_trip", return_value=self.trip)
		)
		self.settings = frappe._dict(enabled=1, mobile_map_provider="Google Maps", allow_driver_navigation=1)
		self.enterContext(patch.object(frappe, "get_cached_doc", return_value=self.settings))
		self.point = self.enterContext(
			patch.object(navigation, "_address_point", return_value={"latitude": -1, "longitude": 37})
		)
		self.reserve = self.enterContext(patch.object(navigation, "reserve_navigation_start"))

	def test_context_is_free_and_credentials_are_not_exposed(self):
		result = navigation.get_navigation_destination("INSTALL", "TRIP", "STOP", "PHONE")
		self.driver.assert_called_once_with("INSTALL", "PHONE")
		self.assigned.assert_called_once_with("TRIP", "DRIVER")
		self.point.assert_called_once_with("ADDRESS")
		self.reserve.assert_not_called()
		self.assertEqual(set(result), {"version", "trip_name", "stop_name", "title", "latitude", "longitude"})

	def test_explicit_start_reserves_exactly_one_destination_without_writes(self):
		with (
			patch.object(frappe.db, "set_value", side_effect=AssertionError("No business writes")),
			patch.object(frappe, "get_roles", side_effect=AssertionError("No planning role required")),
		):
			navigation.get_navigation_destination("INSTALL", "TRIP", "STOP", start=True)
		self.reserve.assert_called_once_with(self.settings)

	def test_device_and_trip_authorization_precede_coordinates_and_quota(self):
		for guard in (self.driver, self.assigned):
			guard.side_effect = ValueError("Denied")
			with self.assertRaises(ValueError):
				navigation.get_navigation_destination("INSTALL", "TRIP", "STOP", start=True)
			guard.side_effect = None
		self.point.assert_not_called()
		self.reserve.assert_not_called()

	def test_wrong_stop_or_finished_trip_cannot_start(self):
		with self.assertRaises(ValueError):
			navigation.get_navigation_destination("INSTALL", "TRIP", "OTHER", start=True)
		for values in ({"vuna_actual_completion": "2026-11-19"}, {"status": "Completed"}):
			self.trip.update(values)
			with self.assertRaises(ValueError):
				navigation.get_navigation_destination("INSTALL", "TRIP", "STOP", start=True)
		self.reserve.assert_not_called()

	def test_disabled_navigation_or_non_google_map_cannot_start(self):
		for field, value in (
			("enabled", 0),
			("allow_driver_navigation", 0),
			("allow_driver_navigation", None),
			("mobile_map_provider", "OpenStreetMap"),
		):
			old = self.settings[field]
			self.settings[field] = value
			with self.assertRaises(ValueError):
				navigation.get_navigation_destination("INSTALL", "TRIP", "STOP", start=True)
			self.settings[field] = old
		self.reserve.assert_not_called()

	def test_missing_coordinates_do_not_consume_navigation_quota(self):
		self.point.side_effect = ValueError("Missing exact coordinates")
		with self.assertRaises(ValueError):
			navigation.get_navigation_destination("INSTALL", "TRIP", "STOP", start=True)
		self.reserve.assert_not_called()

	def test_quota_denial_does_not_return_a_destination(self):
		self.reserve.side_effect = ValueError("Limit reached")
		with self.assertRaises(ValueError):
			navigation.get_navigation_destination("INSTALL", "TRIP", "STOP", start=True)


class TestNavigationQuota(unittest.TestCase):
	def test_atomic_reservation_has_a_separate_site_scoped_counter(self):
		key = frappe.cache.make_key("test:navigation:" + uuid4().hex)
		try:
			self.assertEqual(frappe.cache.eval(navigation.RESERVE_NAVIGATION, 1, key, 1), 1)
			self.assertEqual(frappe.cache.eval(navigation.RESERVE_NAVIGATION, 1, key, 1), 0)
			self.assertGreater(frappe.cache.ttl(key), 0)
		finally:
			frappe.cache.delete(key)

	def test_unavailable_usage_store_fails_closed(self):
		with (
			patch.object(frappe.cache, "eval", side_effect=RedisError),
			patch.object(frappe, "throw", side_effect=ValueError),
		):
			with self.assertRaises(ValueError):
				navigation.reserve_navigation_start(frappe._dict())
