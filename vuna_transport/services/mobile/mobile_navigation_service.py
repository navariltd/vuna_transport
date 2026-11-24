"""Authorize one selected destination; never complete or replan transport work."""

import frappe
from frappe import _
from frappe.utils import cint, nowdate
from redis.exceptions import RedisError

from vuna_transport.services.mobile.mobile_device_service import _get_assigned_trip, _require_mobile_driver
from vuna_transport.services.mobile.mobile_map_service import get_mobile_map_configuration
from vuna_transport.services.mobile.mobile_road_route_service import _address_point
from vuna_transport.services.routing.routing_policy_service import get_limit

# Navigation SDK destinations have their own billing, separate from Routes API.
# Count attempts conservatively, including failed starts. Never restore on rollback.
RESERVE_NAVIGATION = """
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
if used >= tonumber(ARGV[1]) then return 0 end
redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], 172800)
return 1
"""


def reserve_navigation_start(settings):
	limit = get_limit(settings, "navigation_daily_start_limit")
	failed = False
	try:
		key = frappe.cache.make_key("vuna_transport:navigation:starts:" + nowdate())
		result = frappe.cache.eval(RESERVE_NAVIGATION, 1, key, limit)
	except RedisError:
		failed = True
	if failed:
		frappe.throw(_("Navigation usage could not be checked. Try again later."))
	if result != 1:
		frappe.throw(_("The daily navigation start limit has been reached. Contact your administrator."))


def get_navigation_destination(installation_id, trip_name, stop_name, device_identifier=None, *, start=False):
	driver = _require_mobile_driver(installation_id, device_identifier)
	trip = _get_assigned_trip(trip_name, driver.name)
	stop = next((row for row in trip.delivery_stops if row.name == stop_name), None)
	if not stop:
		frappe.throw(_("This stop does not belong to your assigned trip."), frappe.PermissionError)
	if trip.get("vuna_actual_completion") or trip.get("status") in ("Completed", "Cancelled"):
		frappe.throw(_("This trip has already finished."))
	settings = frappe.get_cached_doc("Transport Settings")
	if (
		not cint(settings.get("enabled"))
		or not cint(settings.get("allow_driver_navigation"))
		or get_mobile_map_configuration(settings)["provider"] != "google"
	):
		frappe.throw(_("In-app Google navigation is disabled in Transport Settings."), frappe.PermissionError)
	point = _address_point(stop.address)
	if start:
		reserve_navigation_start(settings)
	return {
		"version": 1,
		"trip_name": trip.name,
		"stop_name": stop.name,
		"title": stop.get("customer") or stop.address,
		"latitude": point["latitude"],
		"longitude": point["longitude"],
	}
