"""Mobile map selection is independent, refreshable and credential-free."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from vuna_transport.services.mobile import mobile_device_service as mobile
from vuna_transport.services.mobile import mobile_map_service as maps
from vuna_transport.vuna_transport.doctype.transport_settings import transport_settings as settings_controller


class TestMobileMapConfiguration(unittest.TestCase):
	def test_automatic_selection_follows_routing_provider(self):
		for routing, expected in (
			("Google", "google"),
			("Geographic Estimate", "openstreetmap"),
			(None, "openstreetmap"),
		):
			with self.subTest(routing=routing):
				result = maps.get_mobile_map_configuration(frappe._dict(routing_provider=routing))
				self.assertEqual(result, {"version": 1, "selection": "Automatic", "provider": expected})

	def test_explicit_mobile_selection_overrides_routing_without_changing_it(self):
		for selection, routing, expected in (
			("OpenStreetMap", "Google", "openstreetmap"),
			("Google Maps", "Geographic Estimate", "google"),
		):
			with self.subTest(selection=selection):
				settings = frappe._dict(mobile_map_provider=selection, routing_provider=routing)
				self.assertEqual(maps.get_mobile_map_configuration(settings)["provider"], expected)
				self.assertEqual(settings.routing_provider, routing)

	def test_unknown_stored_selection_does_not_silently_enable_google(self):
		result = maps.get_mobile_map_configuration(
			frappe._dict(mobile_map_provider="Unknown", routing_provider="Google")
		)
		self.assertEqual(result, {"version": 1, "selection": "OpenStreetMap", "provider": "openstreetmap"})

	def test_mobile_contract_contains_only_allowlisted_preference_values(self):
		settings = frappe._dict(
			mobile_map_provider="Google Maps",
			routing_provider="Google",
			google_routes_api_key="server-secret",
			google_optimization_service_account="private-service-account-json",
		)
		settings.get_password = Mock(side_effect=AssertionError("Passwords must never be requested"))
		result = maps.get_mobile_map_configuration(settings)
		self.assertEqual(set(result), {"version", "selection", "provider"})
		self.assertNotIn("server-secret", str(result))
		self.assertNotIn("private-service-account-json", str(result))
		settings.get_password.assert_not_called()

	def test_settings_are_resolved_per_call_instead_of_a_process_wide_provider(self):
		with patch.object(
			maps.frappe,
			"get_cached_doc",
			side_effect=[
				frappe._dict(routing_provider="Google"),
				frappe._dict(routing_provider="Geographic Estimate"),
			],
		) as cached:
			self.assertEqual(maps.get_mobile_map_configuration()["provider"], "google")
			self.assertEqual(maps.get_mobile_map_configuration()["provider"], "openstreetmap")
			self.assertEqual(cached.call_count, 2)

	def test_empty_explicit_settings_do_not_trigger_a_database_lookup(self):
		with patch.object(maps.frappe, "get_cached_doc") as cached:
			self.assertEqual(maps.get_mobile_map_configuration({})["provider"], "openstreetmap")
			cached.assert_not_called()

	def test_settings_validation_accepts_supported_choices_and_rejects_unknown_values(self):
		settings = frappe._dict(
			routing_provider="Geographic Estimate",
			load_suggestion_option_count=3,
			demand_sources=[frappe._dict(source_document_type="Sales Order", creation_mode="Manual")],
		)
		with (
			patch.object(settings_controller, "_", side_effect=lambda value: value),
			patch.object(settings_controller.frappe, "throw", side_effect=ValueError),
		):
			for choice in (*maps.MOBILE_MAP_PROVIDERS, None, ""):
				settings.mobile_map_provider = choice
				settings_controller.TransportSettings.validate(settings)
			settings.mobile_map_provider = "Unsupported"
			with self.assertRaises(ValueError):
				settings_controller.TransportSettings.validate(settings)


class TestMobileMapDelivery(unittest.TestCase):
	def setUp(self):
		self.device = SimpleNamespace(
			name="DEVICE",
			installation_id="INSTALL",
			latest_installation_id="INSTALL",
			device_identifier="PHONE",
			platform="Android",
			user="driver@example.test",
			status="Active",
		)
		self.config = {"version": 1, "selection": "Automatic", "provider": "google"}
		self.configuration = self.enterContext(
			patch.object(mobile, "get_mobile_map_configuration", return_value=self.config)
		)
		self.enterContext(patch.object(mobile, "_get_rejection_reasons", return_value=[]))
		self.enterContext(patch.object(mobile, "now_datetime", return_value="2026-11-18 16:00:00"))

	def test_allowed_bootstrap_response_includes_map_config(self):
		response = mobile._device_response(self.device, allowed=True)
		self.assertEqual(response["map_config"], self.config)
		self.assertTrue(response["allowed"])
		self.assertEqual(response["device"]["name"], "DEVICE")

	def test_denied_bootstrap_does_not_load_or_return_map_configuration(self):
		response = mobile._device_response(self.device, allowed=False, message="Device revoked")
		self.assertNotIn("map_config", response)
		self.configuration.assert_not_called()

	def test_authorized_work_refresh_includes_current_configuration_without_trips(self):
		assigned = {"linked": True, "trips": []}
		with patch.object(mobile, "get_assigned_delivery_trips", return_value=assigned):
			response = mobile.get_mobile_offline_manifest("INSTALL", "PHONE")
		self.assertEqual(response["map_config"], self.config)
		self.assertEqual(response["assigned"], assigned)
		self.assertEqual(response["delivery_notes"], [])

	def test_unauthorized_work_refresh_does_not_disclose_configuration(self):
		with patch.object(
			mobile, "get_assigned_delivery_trips", return_value={"linked": False, "code": "DEVICE_NOT_ACTIVE"}
		):
			response = mobile.get_mobile_offline_manifest("INSTALL", "PHONE")
		self.assertNotIn("map_config", response)
		self.configuration.assert_not_called()

	def test_refresh_returns_new_configuration_without_changing_device_registration(self):
		mobile._device_response(self.device, allowed=True)
		self.configuration.return_value = {
			"version": 1,
			"selection": "OpenStreetMap",
			"provider": "openstreetmap",
		}
		response = mobile._offline_manifest_response({"linked": True})
		self.assertEqual(response["map_config"]["provider"], "openstreetmap")
		self.assertEqual(self.device.status, "Active")
