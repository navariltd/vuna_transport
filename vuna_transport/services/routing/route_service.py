"""Validation and planning defaults for manually configured transport routes."""

import frappe
from frappe import _
from frappe.utils import flt

from vuna_transport.services.routing.location_service import get_address_planning_data


def validate_transport_route(route):
	sequences = set()
	for index, stop in enumerate(route.route_stops, start=1):
		stop.sequence = stop.sequence or index
		if stop.sequence in sequences:
			frappe.throw(_("Route Stop sequence {0} is duplicated.").format(stop.sequence))
		sequences.add(stop.sequence)
		if flt(stop.estimated_distance_from_previous) < 0 or flt(stop.estimated_travel_time) < 0:
			frappe.throw(_("Route Stop distance and travel time cannot be negative."))
		if stop.address:
			address = get_address_planning_data(stop.address)
			stop.transport_zone = stop.transport_zone or address.delivery_zone
			stop.latitude = address.latitude
			stop.longitude = address.longitude

	route.route_stops.sort(key=lambda stop: stop.sequence)
	if not route.estimated_distance:
		route.estimated_distance = sum(
			flt(stop.estimated_distance_from_previous) for stop in route.route_stops
		)
	if not route.estimated_duration:
		route.estimated_duration = sum(
			flt(stop.estimated_travel_time) for stop in route.route_stops
		)


def apply_route_defaults(delivery_load):
	if not delivery_load.transport_route:
		return
	route = frappe.get_cached_doc("Transport Route", delivery_load.transport_route)
	if route.disabled:
		frappe.throw(_("Transport Route {0} is disabled.").format(route.name))
	if route.transport_zone and delivery_load.transport_zone != route.transport_zone:
		frappe.throw(
			_("Transport Route {0} serves Transport Zone {1}.").format(
				route.name, route.transport_zone
			)
		)
	delivery_load.estimated_distance = delivery_load.estimated_distance or route.estimated_distance
	delivery_load.estimated_duration = delivery_load.estimated_duration or route.estimated_duration
