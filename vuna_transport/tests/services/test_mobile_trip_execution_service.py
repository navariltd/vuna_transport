from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from vuna_transport.services.execution.pod_service import update_delivery_stop_from_pod
from vuna_transport.services.mobile.mobile_device_service import (
	_record_mobile_stop_arrival,
	_set_mobile_stop_actions,
	submit_mobile_proof_of_delivery,
)
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

	def test_started_trip_allows_pod_without_separate_arrival_action(self):
		trip = frappe._dict(
			vuna_actual_start="2026-10-15 08:00:00",
			vuna_actual_completion=None,
		)
		stops = [frappe._dict(visited=0, vuna_actual_arrival=None)]

		_set_mobile_stop_actions(trip, stops)

		self.assertFalse(stops[0].can_arrive)
		self.assertTrue(stops[0].can_complete_delivery)
		self.assertFalse(stops[0].requires_trip_start)

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


class TestOfflineMobilePODSubmission(TestCase):
	@patch(
		"vuna_transport.services.mobile.mobile_device_service._mobile_pod_submission_response",
		return_value={"proof_of_delivery": "POD-0001", "status": "Accepted"},
	)
	@patch("vuna_transport.services.mobile.mobile_device_service.frappe.get_doc")
	@patch(
		"vuna_transport.services.mobile.mobile_device_service.frappe.db.get_value",
		return_value="POD-0001",
	)
	@patch("vuna_transport.services.mobile.mobile_device_service._record_mobile_stop_arrival")
	@patch("vuna_transport.services.mobile.mobile_device_service._get_trip_stop")
	@patch("vuna_transport.services.mobile.mobile_device_service._get_assigned_trip")
	@patch("vuna_transport.services.mobile.mobile_device_service._require_mobile_driver")
	def test_retry_returns_existing_submitted_pod(
		self,
		_require_driver,
		get_trip,
		_get_stop,
		_record_arrival,
		_get_value,
		get_doc,
		_submission_response,
	):
		get_trip.return_value = frappe._dict(name="TRIP-0001")
		get_doc.return_value = frappe._dict(
			name="POD-0001",
			delivery_trip="TRIP-0001",
			delivery_stop="STOP-0001",
			docstatus=1,
		)

		result = submit_mobile_proof_of_delivery(
			"INSTALLATION-1",
			"TRIP-0001",
			"STOP-0001",
			{"request_id": "52f15a24-fd9c-45b7-b395-269353b5ad79"},
		)

		self.assertEqual(result["proof_of_delivery"], "POD-0001")
		_submission_response.assert_called_once()
		_record_arrival.assert_not_called()

	@patch(
		"vuna_transport.services.mobile.mobile_device_service.now_datetime",
		return_value="2026-10-16 08:00:00",
	)
	@patch(
		"vuna_transport.services.mobile.mobile_device_service._mobile_pod_submission_response",
		return_value={"proof_of_delivery": "POD-0001", "status": "Accepted"},
	)
	@patch("vuna_transport.services.mobile.mobile_device_service.create_proof_of_delivery")
	@patch("vuna_transport.services.mobile.mobile_device_service.frappe.get_doc")
	@patch("vuna_transport.services.mobile.mobile_device_service.frappe.db.exists", return_value=False)
	@patch("vuna_transport.services.mobile.mobile_device_service.frappe.db.get_value", return_value=None)
	@patch("vuna_transport.services.mobile.mobile_device_service._record_mobile_stop_arrival")
	@patch("vuna_transport.services.mobile.mobile_device_service._get_trip_stop")
	@patch("vuna_transport.services.mobile.mobile_device_service._get_assigned_trip")
	@patch("vuna_transport.services.mobile.mobile_device_service._require_mobile_driver")
	def test_offline_item_matches_server_pod_by_delivery_note_item(
		self,
		_require_driver,
		get_trip,
		_get_stop,
		_record_arrival,
		_get_value,
		_exists,
		get_doc,
		create_pod,
		_submission_response,
		_now_datetime,
	):
		get_trip.return_value = frappe._dict(name="TRIP-0001")
		create_pod.return_value = "POD-0001"
		item = SimpleNamespace(
			name="POD-ITEM-0001",
			delivery_note_item="DN-ITEM-0001",
			item_code="ITEM-0001",
			delivered_qty=10,
		)
		pod = SimpleNamespace(
			name="POD-0001",
			delivery_trip="TRIP-0001",
			delivery_stop="STOP-0001",
			docstatus=0,
			items=[item],
		)
		pod.save = Mock()
		pod.submit = Mock()
		pod.update = lambda values: [setattr(pod, key, value) for key, value in values.items()]
		self.enterContext(
			patch(
				"vuna_transport.services.mobile.location_evidence.get_system_timezone",
				return_value="Africa/Nairobi",
			)
		)
		get_doc.return_value = pod

		submit_mobile_proof_of_delivery(
			"INSTALLATION-1",
			"TRIP-0001",
			"STOP-0001",
			{
				"request_id": "52f15a24-fd9c-45b7-b395-269353b5ad79",
				"device_location": {
					"status": "Captured",
					"latitude": -1.2,
					"longitude": 36.8,
					"accuracy": 6,
					"captured_at": "2026-10-15T09:00:00Z",
				},
				"items": [
					{
						"name": "DN-ITEM-0001",
						"delivery_note_item": "DN-ITEM-0001",
						"delivered_qty": 7,
						"rejection_reason": "Damaged",
					}
				],
			},
		)

		self.assertEqual(item.delivered_qty, 7)
		self.assertEqual(item.rejection_reason, "Damaged")
		self.assertEqual(pod.mobile_request_id, "52f15a24-fd9c-45b7-b395-269353b5ad79")
		self.assertEqual((pod.latitude, pod.longitude, pod.location_accuracy), (-1.2, 36.8, 6))
		self.assertEqual(pod.location_capture_status, "Captured")
		pod.save.assert_called_once()
		pod.submit.assert_called_once()
