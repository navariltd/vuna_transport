import unittest
from unittest.mock import patch

import frappe

from vuna_transport.services.execution import unplanned_pickup_policy as service


class TestUnplannedPickupPolicy(unittest.TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(service, "frappe"))
		self.settings = frappe._dict()
		self.framework.get_cached_doc.return_value = self.settings
		self.framework.throw.side_effect = lambda message: (_ for _ in ()).throw(ValueError(message))
		self.enterContext(patch.object(service, "_", side_effect=lambda message: message))

	def test_unconfigured_and_disabled_are_off_by_default(self):
		self.assertFalse(service.unplanned_pickups_enabled())
		self.settings.allow_unplanned_return_pickups = 0
		self.assertFalse(service.unplanned_pickups_enabled())
		with self.assertRaisesRegex(ValueError, "approved Return Request"):
			service.require_unplanned_pickups_enabled()

	def test_explicit_opt_in_permits_unplanned_pickups(self):
		self.settings.allow_unplanned_return_pickups = 1
		self.assertTrue(service.unplanned_pickups_enabled())
		service.require_unplanned_pickups_enabled()

	def test_disabling_does_not_hide_existing_stop_history(self):
		self.framework.get_all.return_value = ["STOP-1", "STOP-1"]
		stops = [frappe._dict(name="STOP-1"), frappe._dict(name="STOP-2")]
		service.attach_collection_visibility("TRIP-1", stops)
		self.assertFalse(stops[0].unplanned_pickups_enabled)
		self.assertEqual(stops[0].unplanned_collection_count, 2)
		self.assertEqual(stops[1].unplanned_collection_count, 0)
		self.framework.get_all.assert_called_once_with(
			"Proof of Delivery",
			filters={"delivery_trip": "TRIP-1", "proof_type": "Unplanned Collection", "docstatus": 1},
			pluck="collection_stop",
		)

	def test_enabled_stops_advertise_recording_even_without_previous_reports(self):
		self.settings.allow_unplanned_return_pickups = 1
		self.framework.get_all.return_value = []
		stops = [frappe._dict(name="STOP-1")]
		service.attach_collection_visibility("TRIP-1", stops)
		self.assertTrue(stops[0].unplanned_pickups_enabled)
		self.assertEqual(stops[0].unplanned_collection_count, 0)
