"""Device-reported event locations, separate from planned stop coordinates."""

import json
import math
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import get_system_timezone

STATUSES = {"Captured", "Permission Denied", "Unavailable", "Timed Out"}


def apply_device_location(document, payload):
	data = payload.get("device_location")
	if data is None:
		return  # Desk records and already-queued operations have no device evidence.
	if (
		not isinstance(data, dict)
		or not isinstance(data.get("status"), str)
		or data["status"] not in STATUSES
	):
		frappe.throw(_("Invalid device location evidence."))
	status = data["status"]
	values = {"location_capture_status": status}
	for field in ("latitude", "longitude", "location_accuracy", "location_captured_at"):
		values[field] = None
	if status == "Captured":
		try:
			if not isinstance(data.get("captured_at"), str) or any(
				isinstance(data.get(key), bool) for key in ("latitude", "longitude", "accuracy")
			):
				raise ValueError
			latitude, longitude, accuracy = (
				float(data[key]) for key in ("latitude", "longitude", "accuracy")
			)
			captured = datetime.fromisoformat(data["captured_at"].replace("Z", "+00:00"))
			if (
				not all(math.isfinite(value) for value in (latitude, longitude, accuracy))
				or not -90 <= latitude <= 90
				or not -180 <= longitude <= 180
				or accuracy < 0
				or captured.tzinfo is None
				or captured > datetime.now(UTC) + timedelta(minutes=5)
			):
				raise ValueError
		except (KeyError, TypeError, ValueError, OverflowError):
			frappe.throw(_("Provide valid coordinates, GPS accuracy and a capture time with timezone."))
		values.update(
			latitude=latitude,
			longitude=longitude,
			location_accuracy=accuracy,
			location_captured_at=captured.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None),
			location=json.dumps(
				{
					"type": "FeatureCollection",
					"features": [
						{
							"type": "Feature",
							"properties": {},
							"geometry": {"type": "Point", "coordinates": [longitude, latitude]},
						}
					],
				}
			),
		)
	else:
		if any(data.get(field) is not None for field in ("latitude", "longitude", "accuracy", "captured_at")):
			frappe.throw(_("Unavailable location evidence cannot contain coordinates or a GPS capture time."))
		values["location"] = None
	document.update(values)
