import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.utils import flt

from vuna_transport.services.execution import direct_return_pickup_service as service


def record(**values):
	row = SimpleNamespace(**values)
	row.get = lambda key, default=None: getattr(row, key, default)
	return row


class TestDirectReturnPickup(unittest.TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(service, "frappe"))
		self.framework.throw.side_effect = lambda message, *args: (_ for _ in ()).throw(ValueError(message))
		self.enterContext(patch.object(service, "_", side_effect=lambda message: message))
		self.enterContext(
			patch.object(
				service,
				"flt",
				side_effect=lambda value, precision=None: flt(
					value, precision, rounding_method="Banker's Rounding"
				),
			)
		)
		self.permission = self.enterContext(patch.object(service, "require_document_permission"))
		self.transport_method = self.enterContext(
			patch(
				"vuna_transport.services.demand.delivery_method_service.source_uses_company_transport",
				return_value=True,
			)
		)
		self.policy = self.enterContext(
			patch(
				"vuna_transport.services.demand.source_policy_service.get_source_policy",
				return_value=frappe._dict(required_workflow_state=None),
			)
		)
		self.framework.get_roles.return_value = ["Transport Dispatcher"]
		self.framework.db.get_value.side_effect = (
			lambda doctype, *args, **kwargs: (0, 0) if doctype == "Item" else None
		)
		self.framework.db.exists.return_value = False
		self.framework.db.sql.return_value = []
		self.framework.get_precision.return_value = 6
		self.row = record(
			name="RRI-1",
			item_code="MILK",
			requested_return_qty=2,
			received_qty=0,
			conversion_factor=10,
			uom="Carton",
			stock_uom="Pcs",
			custody_warehouse=None,
		)
		self.request = record(
			name="RR-1",
			docstatus=1,
			company="Company",
			status="Awaiting Receipt",
			return_context="Customer",
			customer="CUSTOMER-1",
			proof_of_delivery=None,
			items=[self.row],
			add_comment=Mock(),
		)
		self.stop = record(name="STOP-1", idx=1, address="ADDRESS-1", visited=0, customer="CUSTOMER-1")
		self.trip = record(
			name="TRIP-1",
			docstatus=1,
			company="Company",
			status="In Transit",
			vuna_actual_completion=None,
			driver="DRIVER-1",
			delivery_stops=[self.stop],
		)
		self.task = Mock(name="task")
		self.task.name = "TASK-1"
		self.framework.new_doc.return_value = self.task
		self.framework.get_doc.side_effect = (
			lambda doctype, name: self.request if doctype == "Return Request" else self.trip
		)
		self.original_stop = service.validate_stop
		self.match_stop = self.enterContext(patch.object(service, "validate_stop", return_value=self.stop))

	def test_assignment_creates_task_with_no_planning_references(self):
		self.assertEqual(service.assign_return_pickup("RR-1", "TRIP-1", "STOP-1"), "TASK-1")
		values = self.task.update.call_args.args[0]
		self.assertEqual(
			(values["return_request"], values["delivery_trip"], values["delivery_stop"]),
			("RR-1", "TRIP-1", "STOP-1"),
		)
		self.assertNotIn("transport_load", values)
		self.assertNotIn("transport_demand", values)
		self.framework.new_doc.assert_called_once_with("Transport Trip Task")

		self.task.set.assert_called_once_with(
			"items",
			[
				{
					"return_request_item": "RRI-1",
					"item_code": "MILK",
					"qty": 2,
					"uom": "Carton",
					"stock_uom": "Pcs",
					"conversion_factor": 10,
					"stock_qty": 20,
				}
			],
		)

	def test_counterparty_arranged_return_cannot_be_assigned_to_company_driver(self):
		self.transport_method.return_value = False
		with self.assertRaisesRegex(ValueError, "does not require a company pickup"):
			service.assign_return_pickup("RR-1", "TRIP-1", "STOP-1")
		self.framework.new_doc.assert_not_called()

	def test_repeat_assignment_is_idempotent(self):
		self.framework.db.get_value.side_effect = (
			lambda doctype, *args, **kwargs: "TASK-1" if doctype == "Transport Trip Task" else None
		)
		self.assertEqual(service.assign_return_pickup("RR-1", "TRIP-1", "STOP-1"), "TASK-1")
		self.framework.new_doc.assert_not_called()

	def test_submitted_approval_is_required(self):
		self.request.docstatus = 0
		with self.assertRaisesRegex(ValueError, "submitted Return Request"):
			service.assign_return_pickup("RR-1", "TRIP-1", "STOP-1")

	def test_configured_workflow_approval_is_not_bypassed(self):
		self.policy.return_value.required_workflow_state = "Approved"
		self.request.workflow_state = "Pending Approval"
		with self.assertRaisesRegex(ValueError, "approval state"):
			service.assign_return_pickup("RR-1", "TRIP-1", "STOP-1")

	def test_driver_cannot_assign_their_own_pickups(self):
		self.framework.get_roles.return_value = ["Driver"]
		with self.assertRaisesRegex(ValueError, "dispatch staff"):
			service.assign_return_pickup("RR-1", "TRIP-1", "STOP-1")

	def test_completed_trip_cross_company_and_missing_driver_are_rejected(self):
		for field, value in (("status", "Completed"), ("company", "Other"), ("driver", None)):
			with self.subTest(field=field):
				old = getattr(self.trip, field)
				setattr(self.trip, field, value)
				with self.assertRaisesRegex(ValueError, "active submitted trip"):
					service.assign_return_pickup("RR-1", "TRIP-1", "STOP-1")
				setattr(self.trip, field, old)

	def test_completed_stop_cannot_receive_new_work(self):
		self.stop.visited = 1
		with self.assertRaisesRegex(ValueError, "not completed"):
			service.assign_return_pickup("RR-1", "TRIP-1", "STOP-1")

	def test_concurrent_active_assignment_is_rejected(self):
		self.framework.db.exists.return_value = True
		with self.assertRaisesRegex(ValueError, "active direct pickup"):
			service.assign_return_pickup("RR-1", "TRIP-1", "STOP-1")

	def test_existing_planning_must_be_released_first(self):
		self.framework.db.exists.side_effect = lambda doctype, *args, **kwargs: doctype == "Transport Demand"
		with self.assertRaisesRegex(ValueError, "existing pickup demand"):
			service.assign_return_pickup("RR-1", "TRIP-1", "STOP-1")

	def test_partial_collection_only_assigns_remaining_quantity(self):
		with patch.object(service, "collected_quantities", return_value={"RRI-1": 10}):
			self.assertEqual(service.remaining_items(self.request)[0]["qty"], 1)
		self.assertEqual(self.row.requested_return_qty, 2)

	def test_received_goods_cannot_be_picked_up_again(self):
		self.row.received_qty = 2
		self.assertEqual(service.remaining_items(self.request), [])

	def test_unrelated_customer_stop_is_rejected(self):
		with patch(
			"vuna_transport.services.execution.collection_reference_service.stop_context",
			return_value={"customer": "OTHER-CUSTOMER", "warehouses": []},
		):
			with self.assertRaisesRegex(ValueError, "Return Request customer"):
				self.original_stop(self.request, self.trip, "STOP-1")

	def test_return_request_cannot_cancel_with_live_assignment(self):
		self.framework.db.exists.return_value = True
		with self.assertRaisesRegex(ValueError, "Remove the direct pickup"):
			service.guard_return_cancellation(self.request)

	def test_stop_options_exclude_unrelated_and_completed_customer_stops(self):
		self.trip.delivery_stops = [
			self.stop,
			record(name="OTHER", idx=2, address="OTHER", customer="OTHER", visited=0),
			record(name="DONE", idx=3, address="DONE", customer="CUSTOMER-1", visited=1),
		]
		self.assertEqual([row["value"] for row in service.get_assignment_stops("RR-1", "TRIP-1")], ["STOP-1"])

	def test_depot_stop_options_use_exact_depot_not_arbitrary_trip_stops(self):
		self.request.return_context = "Depot"
		self.trip.delivery_stops = [
			self.stop,
			record(name="OTHER", idx=2, address="OTHER", visited=0, customer=None),
		]
		with (
			patch.object(service, "_source_warehouse", return_value="DEPOT-1"),
			patch(
				"vuna_transport.services.execution.collection_reference_service.stop_context",
				side_effect=lambda trip, name, **kwargs: {
					"warehouses": ["DEPOT-1" if name == "STOP-1" else "DEPOT-2"]
				},
			),
		):
			self.assertEqual(
				[row["value"] for row in service.get_assignment_stops("RR-1", "TRIP-1")], ["STOP-1"]
			)

	def test_holding_warehouse_matches_its_own_depot_stop(self):
		self.request.return_context = "Depot"
		self.framework.db.get_value.side_effect = (
			lambda dt, name, field: "HOLDING-1"
			if name == "DEPOT-1" and field == "vuna_returns_warehouse"
			else None
		)
		with (
			patch.object(service, "_source_warehouse", return_value="HOLDING-1"),
			patch(
				"vuna_transport.services.execution.collection_reference_service.stop_context",
				return_value={"warehouses": ["DEPOT-1"]},
			),
		):
			self.assertEqual(len(service.get_assignment_stops("RR-1", "TRIP-1")), 1)

	def test_unidentified_location_is_not_a_matching_depot_stop(self):
		self.request.return_context = "Depot"
		with (
			patch.object(service, "_source_warehouse", return_value="DEPOT-1"),
			patch(
				"vuna_transport.services.execution.collection_reference_service.stop_context",
				return_value={"warehouses": []},
			) as context,
		):
			self.assertEqual(service.get_assignment_stops("RR-1", "TRIP-1"), [])
			context.assert_called_once_with(self.trip, self.stop.name, allow_empty=True)

	def test_stop_lookup_rejects_inactive_trip(self):
		self.trip.vuna_actual_completion = "2026-11-05"
		with self.assertRaisesRegex(ValueError, "active submitted trip"):
			service.get_assignment_stops("RR-1", "TRIP-1")

	def test_trip_lookup_filters_matching_stops_before_pagination(self):
		wrong = record(**{**self.trip.__dict__, "name": "WRONG", "has_permission": Mock(return_value=True)})
		self.trip.has_permission = Mock(return_value=True)
		self.framework.get_list.return_value = [wrong.name, self.trip.name]
		self.framework.get_doc.side_effect = (
			lambda dt, name: self.request
			if dt == "Return Request"
			else wrong
			if name == wrong.name
			else self.trip
		)
		with patch.object(
			service,
			"_matching_stops",
			side_effect=lambda req, trip: [] if trip.name == "WRONG" else [{"value": "STOP-1"}],
		):
			self.assertEqual(
				service.get_assignment_trip_query(
					"Delivery Trip", "", "name", 0, 1, {"return_request": "RR-1"}
				),
				[("TRIP-1", "DRIVER-1")],
			)
		self.trip.has_permission.assert_called_once_with("write")

	def test_trip_lookup_excludes_trips_without_write_permission(self):
		self.trip.has_permission = Mock(return_value=False)
		self.framework.get_list.return_value = [self.trip.name]
		with patch.object(service, "_matching_stops", return_value=[{"value": "STOP-1"}]) as matches:
			self.assertEqual(
				service.get_assignment_trip_query(
					"Delivery Trip", "", "name", 0, 10, {"return_request": "RR-1"}
				),
				[],
			)
			matches.assert_not_called()
