"""Shared time-window helpers for dispatch resources."""

from frappe.utils import get_datetime


def time_windows_overlap(first, second):
	"""Return whether two half-open planning windows overlap."""
	first_start, first_end = _window_values(first)
	second_start, second_end = _window_values(second)
	if not all((first_start, first_end, second_start, second_end)):
		return False
	return get_datetime(first_start) < get_datetime(second_end) and get_datetime(second_start) < get_datetime(
		first_end
	)


def interval_overlaps(from_time, to_time, other_from, other_to):
	return time_windows_overlap(
		{"planned_departure": from_time, "planned_arrival": to_time},
		{"planned_departure": other_from, "planned_arrival": other_to},
	)


def _window_values(value):
	if isinstance(value, dict):
		return value.get("planned_departure"), value.get("planned_arrival")
	return getattr(value, "planned_departure", None), getattr(value, "planned_arrival", None)
