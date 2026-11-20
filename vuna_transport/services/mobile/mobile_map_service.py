"""Map preferences and separately authorized, customer-owned mobile SDK keys."""

import frappe
from frappe import _
from frappe.utils import cint

MOBILE_MAP_PROVIDERS = ("Automatic", "OpenStreetMap", "Google Maps")


def get_mobile_map_configuration(settings=None):
	"""Return the small, versioned contract consumed by compatible mobile releases.

	Map display is independent of permission to call the server's paid routing APIs.
	Unknown stored choices fail safely to the existing open map, not a paid provider.
	"""
	if settings is None:
		settings = frappe.get_cached_doc("Transport Settings")
	selection = settings.get("mobile_map_provider") or "Automatic"
	if selection not in MOBILE_MAP_PROVIDERS:
		selection = "OpenStreetMap"
	use_google = selection == "Google Maps" or (
		selection == "Automatic" and settings.get("routing_provider") == "Google"
	)
	return {"version": 1, "selection": selection, "provider": "google" if use_google else "openstreetmap"}


def get_mobile_google_maps_key(installation_id, platform, device_identifier=None):
	# Lazy import avoids the device service's preference-helper import cycle.
	from vuna_transport.services.mobile.mobile_device_service import _require_mobile_driver

	_require_mobile_driver(installation_id, device_identifier)
	if platform not in ("android", "ios"):
		frappe.throw(_("Choose a supported mobile platform."))
	settings = frappe.get_cached_doc("Transport Settings")
	if not cint(settings.get("enabled")) or get_mobile_map_configuration(settings)["provider"] != "google":
		frappe.throw(_("Google mobile maps are disabled for this site."), frappe.PermissionError)
	# Never substitute the server Routes key, service-account credentials, or a
	# vendor key. Mobile keys must be restricted to the signed app identity.
	field = "google_maps_android_api_key" if platform == "android" else "google_maps_ios_api_key"
	key = settings.get_password(field, raise_exception=False)
	if not key or not key.strip():
		frappe.throw(
			_(
				"Ask your administrator to configure the {0} mobile Google Maps key in Transport Settings."
			).format(platform)
		)
	return {"version": 1, "platform": platform, "api_key": key.strip()}
