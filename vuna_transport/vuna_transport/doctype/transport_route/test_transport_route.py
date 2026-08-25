# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from types import SimpleNamespace

from frappe.tests import UnitTestCase

from vuna_transport.services.routing.route_service import validate_transport_route


# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]



class TestTransportRoute(UnitTestCase):
	def test_route_totals_and_sequences_are_derived_from_stops(self):
		route = SimpleNamespace(
			route_stops=[
				SimpleNamespace(
					sequence=0,
					address=None,
					estimated_distance_from_previous=12,
					estimated_travel_time=0.5,
				),
				SimpleNamespace(
					sequence=0,
					address=None,
					estimated_distance_from_previous=18,
					estimated_travel_time=1,
				),
			],
			estimated_distance=0,
			estimated_duration=0,
		)
		validate_transport_route(route)
		self.assertEqual([stop.sequence for stop in route.route_stops], [1, 2])
		self.assertEqual(route.estimated_distance, 30)
		self.assertEqual(route.estimated_duration, 1.5)
