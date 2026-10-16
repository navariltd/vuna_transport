from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from vuna_transport.services.execution.pod_service import update_delivery_stop_from_pod
from vuna_transport.services.mobile.mobile_device_service import _record_mobile_stop_arrival
from vuna_transport.services.mobile.mobile_trip_execution_service import (
	get_mobile_execution_actions,
	get_mobile_execution_status,
	synchronize_mobile_trip_execution_status,
)


class TestMobileTripExecutionStatus(TestCase):
	def test_scheduled_trip_can_start(self):
		trip = frappe._dict(docstatus=1, vuna_actual_start=None, vuna_actual_completion=None)
		stops = [frappe._dict(visited=0)]

		self.assertEqual(get_mobile_execution_status(trip, stops), "Scheduled")
		self.assertTrue(get_mobile_execution_actions(trip, stops)["can_start"])

	def test_started_trip_is_in_transit_and_can_report_incident(self):
		trip = frappe._dict(
			docstatus=1,
			vuna_actual_start="2026-10-15 08:00:00",
			vuna_actual_completion=None,
		)
		stops = [frappe._dict(visited=0)]

		self.assertEqual(get_mobile_execution_status(trip, stops), "In Transit")
		self.assertTrue(get_mobile_execution_actions(trip, stops)["can_report_incident"])

	def test_existing_completed_trip_does_not_await_driver_completion(self):
		trip = frappe._dict(
			docstatus=1,
			status="Completed",
			vuna_actual_start="2026-10-15 08:00:00",
			vuna_actual_completion=None,
		)
		stops = [frappe._dict(visited=1), frappe._dict(visited=1)]

		self.assertEqual(get_mobile_execution_status(trip, stops), "Completed")
		self.assertFalse(any(get_mobile_execution_actions(trip, stops).values()))

	def test_completion_timestamp_wins_over_standard_status(self):
		trip = frappe._dict(
			docstatus=1,
			vuna_actual_start="2026-10-15 08:00:00",
			vuna_actual_completion="2026-10-15 16:00:00",
		)
		stops = [frappe._dict(visited=1)]

		self.assertEqual(get_mobile_execution_status(trip, stops), "Completed")
		self.assertFalse(any(get_mobile_execution_actions(trip, stops).values()))

	@patch("vuna_transport.services.mobile.mobile_trip_execution_service.frappe.db.set_value")
	def test_started_trip_remains_in_transit_after_standard_status_recalculation(self, set_value):
		trip = frappe._dict(
			name="TRIP-0001",
			docstatus=1,
			status="Scheduled",
			vuna_actual_start="2026-10-15 08:00:00",
			vuna_actual_completion=None,
			delivery_stops=[frappe._dict(visited=0)],
		)

		synchronize_mobile_trip_execution_status(trip)

		set_value.assert_called_once_with(
			"Delivery Trip",
			"TRIP-0001",
			"status",
			"In Transit",
			update_modified=False,
		)
		self.assertEqual(trip.status, "In Transit")

	@patch(
		"vuna_transport.services.mobile.mobile_device_service.now_datetime",
		return_value="2026-10-15 09:00:00",
	)
	@patch("vuna_transport.services.mobile.mobile_device_service.frappe.db.set_value")
	def test_beginning_pod_records_arrival(self, set_value, _now_datetime):
		trip = frappe._dict(
			name="TRIP-0001",
			vuna_actual_start="2026-10-15 08:00:00",
			vuna_actual_completion=None,
		)
		stop = frappe._dict(name="STOP-0001", visited=0, vuna_actual_arrival=None)

		_record_mobile_stop_arrival(trip, stop)

		set_value.assert_called_once_with(
			"Delivery Stop",
			"STOP-0001",
			"vuna_actual_arrival",
			"2026-10-15 09:00:00",
			update_modified=False,
		)
		self.assertEqual(stop.vuna_actual_arrival, "2026-10-15 09:00:00")

	@patch("vuna_transport.services.execution.pod_service.recompute_delivery_load_completion")
	@patch(
		"vuna_transport.services.execution.pod_service._all_delivery_trip_stops_visited",
		return_value=True,
	)
	@patch(
		"vuna_transport.services.execution.pod_service.frappe.db.get_value",
		return_value="2026-10-15 09:00:00",
	)
	@patch("vuna_transport.services.execution.pod_service.frappe.db.set_value")
	@patch("vuna_transport.services.execution.pod_service.frappe.get_doc")
	def test_final_pod_completes_started_trip(
		self,
		get_doc,
		_set_value,
		_get_value,
		_all_stops_visited,
		_recompute_load,
	):
		trip = frappe._dict(
			name="TRIP-0001",
			status="In Transit",
			vuna_actual_start="2026-10-15 08:00:00",
		)
		trip.update_status = Mock()
		trip.db_set = Mock()
		get_doc.return_value = trip
		pod = frappe._dict(
			name="POD-0001",
			delivery_stop="STOP-0001",
			delivery_trip="TRIP-0001",
			delivery_load="LOAD-0001",
			delivery_order=None,
			delivery_datetime="2026-10-15 16:00:00",
		)

		update_delivery_stop_from_pod(pod, "Accepted")

		trip.db_set.assert_called_once_with(
			{
				"vuna_actual_completion": "2026-10-15 16:00:00",
				"status": "Completed",
			},
			update_modified=False,
		)
