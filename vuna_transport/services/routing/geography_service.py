"""Deterministic geographic and delivery-window scoring for load planning."""

from math import asin, cos, radians, sin, sqrt

from frappe.utils import flt


def haversine_distance_km(first_latitude, first_longitude, second_latitude, second_longitude):
	"""Return straight-line distance in kilometres between two WGS84 points."""
	latitude_delta = radians(flt(second_latitude) - flt(first_latitude))
	longitude_delta = radians(flt(second_longitude) - flt(first_longitude))
	first_latitude = radians(flt(first_latitude))
	second_latitude = radians(flt(second_latitude))
	value = (
		sin(latitude_delta / 2) ** 2
		+ cos(first_latitude) * cos(second_latitude) * sin(longitude_delta / 2) ** 2
	)
	return 6371 * 2 * asin(sqrt(value))


def calculate_geographic_cohesion(orders, distance_scale_km=50):
	"""Score destination cohesion without pretending missing coordinates are evidence."""
	orders = list(orders)
	coordinates = [
		(flt(order.destination_latitude), flt(order.destination_longitude))
		for order in orders
		if order.destination_latitude not in (None, "") and order.destination_longitude not in (None, "")
	]
	missing_count = len(orders) - len(coordinates)
	if len(coordinates) < 2:
		return {
			"score": 55 if coordinates else 35,
			"average_distance_km": None,
			"maximum_distance_km": None,
			"missing_coordinate_count": missing_count,
		}

	distances = []
	for index, first in enumerate(coordinates):
		for second in coordinates[index + 1 :]:
			distances.append(haversine_distance_km(*first, *second))
	average_distance = sum(distances) / len(distances)
	maximum_distance = max(distances)
	score = max(0, 100 - average_distance / distance_scale_km * 100)
	if missing_count:
		score *= max(0.5, len(coordinates) / len(orders))
	return {
		"score": round(score, 2),
		"average_distance_km": round(average_distance, 2),
		"maximum_distance_km": round(maximum_distance, 2),
		"missing_coordinate_count": missing_count,
	}


def calculate_delivery_window_compatibility(orders):
	"""Score whether configured delivery windows overlap; absent windows are neutral."""
	windows = [
		(_seconds(order.delivery_from_time), _seconds(order.delivery_to_time))
		for order in orders
		if order.delivery_from_time and order.delivery_to_time
	]
	if not windows:
		return {"score": 60, "configured_count": 0, "overlap": None}
	if len(windows) == 1:
		return {"score": 70, "configured_count": 1, "overlap": True}

	latest_start = max(start for start, _end in windows)
	earliest_end = min(end for _start, end in windows)
	if latest_start <= earliest_end:
		return {"score": 100, "configured_count": len(windows), "overlap": True}
	return {"score": 25, "configured_count": len(windows), "overlap": False}


def _seconds(value):
	if hasattr(value, "hour"):
		return value.hour * 3600 + value.minute * 60 + value.second
	parts = [int(part) for part in str(value).split(":")]
	return parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0)
