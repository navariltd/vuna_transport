import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from vuna_transport.services.execution import allocation_transfer_service as service
from vuna_transport.services.mobile import mobile_device_service


class TestAllocationTransferDefaults(unittest.TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(service, "frappe"))
		self.framework.throw.side_effect = ValueError
		self.enterContext(patch.object(service, "_", side_effect=lambda value: value))
		self.enterContext(patch.object(service, "require_document_permission"))
		self.allocation = frappe._dict(name="VA-1", company="Company", vehicle="VEH-1", driver="DR-1")

	def test_one_vehicle_transit_warehouse_is_selected(self):
		self.framework.get_all.return_value = ["VEH-TRANSIT"]
		self.framework.get_doc.return_value = frappe._dict(name="VEH-TRANSIT")
		self.assertEqual(service.resolve_vehicle_transit_warehouse(self.allocation), "VEH-TRANSIT")
		filters = self.framework.get_all.call_args.kwargs["filters"]
		self.assertEqual(filters["vehicle"], "VEH-1")
		self.assertEqual(filters["warehouse_type"], "Transit")

	def test_missing_or_ambiguous_vehicle_warehouses_are_not_guessed(self):
		for names in ([], ["TRANSIT-1", "TRANSIT-2"]):
			self.framework.get_all.return_value = names
			with self.assertRaises(ValueError):
				service.resolve_vehicle_transit_warehouse(self.allocation)

	def test_transfer_detail_serialization_accepts_document_not_only_dict(self):
		trip = SimpleNamespace(
			name="TRIP-1", vuna_task_based=1, vuna_actual_start=None, vuna_actual_completion=None
		)
		stop = frappe._dict(name="STOP-1", customer=None, address="Depot", delivery_note=None)
		task = frappe._dict(
			name="TASK-1", delivery_stop="STOP-1", task_type="Transfer Receipt", status="Ready"
		)
		with patch.object(mobile_device_service.frappe, "get_all", return_value=[task]):
			mobile_device_service._attach_transport_tasks(trip, [stop])
		self.assertEqual(stop.tasks[0].operation_label, "Depot delivery")
		self.assertEqual(stop.location_label, "Depot")

	def test_legacy_loading_stops_are_not_driver_deliveries(self):
		trip = SimpleNamespace(
			name="TRIP-1", vuna_task_based=1, vuna_actual_start=None, vuna_actual_completion=None
		)
		stops = [
			frappe._dict(name="LOAD", address="Main", delivery_note=None),
			frappe._dict(name="DROP", address="Depot", delivery_note=None),
		]
		tasks = [
			frappe._dict(name="OLD", delivery_stop="LOAD", task_type="Transfer Dispatch", status="Ready"),
			frappe._dict(name="NEW", delivery_stop="DROP", task_type="Transfer Receipt", status="Ready"),
		]
		with patch.object(mobile_device_service.frappe, "get_all", return_value=tasks):
			mobile_device_service._attach_transport_tasks(trip, stops)
		self.assertEqual([stop.name for stop in stops], ["DROP"])

	def test_cancellation_locks_trip_before_checking_departure(self):
		self.framework.db.get_value.side_effect = ["TRIP-1", "2026-11-04"]
		with self.assertRaises(ValueError):
			service.guard_allocation_stock_cancellation(
				frappe._dict(vuna_vehicle_allocation="VA-1", vuna_transport_load="TL-1")
			)
		self.assertTrue(self.framework.db.get_value.call_args.kwargs["for_update"])
