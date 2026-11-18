"""Public map display preferences; native SDK credentials never leave their build."""

import frappe

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
