# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from frappe.exceptions import ValidationError
from frappe.tests import UnitTestCase

from vuna_transport.services.fleet.vehicle_allocation_service import (
	_get_replacement_stage,
	_validate_initial_allocation_sequence,
	_validate_planning_window,
	_validate_subcontractor,
	get_allocation_delivery_note_status,
	get_allocation_load_names,
	_get_allocation_execution_status,
	get_load_resource_window,
	prepare_allocation_cancellation,
	sync_allocation_execution_status,
	sync_allocation_execution_status_for_delivery_note,
	time_windows_overlap,
)
from vuna_transport.vuna_transport.doctype.vehicle_allocation.vehicle_allocation import (
	VehicleAllocation,
	snapshot_transport_cost,
)


class TestVehicleAllocation(UnitTestCase):
	@patch("vuna_transport.services.fleet.vehicle_allocation_service.sync_allocation_execution_status")
	@patch("vuna_transport.services.fleet.vehicle_allocation_service.frappe.get_all")
	def test_delivery_note_status_sync_uses_delivery_load_order_lineage(self, get_all, sync_status):
		get_all.side_effect = [["DLO-001"], ["DL-001"], ["VA-001"]]

		sync_allocation_execution_status_for_delivery_note(
			SimpleNamespace(name="DN-001", vuna_delivery_load=None)
		)

		self.assertEqual(get_all.call_args_list[0].kwargs["pluck"], "vuna_delivery_load_order")
		sync_status.assert_called_once_with("VA-001")

	@patch("vuna_transport.services.fleet.vehicle_allocation_service._get_allocation_execution_state")
	def test_execution_status_is_saved_for_standard_list_filter(self, get_execution_state):
		allocation = SimpleNamespace(docstatus=1, status="Allocated", db_set=MagicMock())
		get_execution_state.return_value = {"execution_status": "Ready for Delivery Trip"}

		sync_allocation_execution_status(allocation)

		allocation.db_set.assert_called_once_with(
			"status", "Ready for Delivery Trip", update_modified=False
		)
		self.assertEqual(allocation.status, "Ready for Delivery Trip")

	def test_execution_status_is_derived_from_delivery_note_progress(self):
		allocation = SimpleNamespace(docstatus=1, status="Allocated")

		self.assertEqual(
			_get_allocation_execution_status(
				allocation,
				[
					{
						"ready": False,
						"active_delivery_notes": ["DN-001"],
					}
				],
			),
			"Awaiting Delivery Notes",
		)
		self.assertEqual(
			_get_allocation_execution_status(
				allocation,
				[{"ready": True, "active_delivery_notes": ["DN-001"]}],
			),
			"Ready for Delivery Trip",
		)

	def test_execution_status_uses_delivery_trip_progress(self):
		allocation = SimpleNamespace(docstatus=1, status="Allocated")
		trip = SimpleNamespace(docstatus=1, status="In Transit")

		self.assertEqual(_get_allocation_execution_status(allocation, [], trip), "In Transit")

	@patch("vuna_transport.services.fleet.vehicle_allocation_service.get_delivery_note_submission_status")
	@patch("vuna_transport.services.fleet.vehicle_allocation_service.get_allocation_loads")
	@patch("vuna_transport.services.fleet.vehicle_allocation_service.require_document_permission")
	@patch("vuna_transport.services.fleet.vehicle_allocation_service.frappe.get_doc")
	@patch("vuna_transport.services.fleet.vehicle_allocation_service.frappe.db.get_value")
	def test_delivery_trip_action_requires_all_allocation_notes_to_be_submitted(
		self, get_value, get_doc, require_permission, get_loads, get_note_status
	):
		get_value.return_value = None
		allocation = SimpleNamespace(name="VA-1")
		get_doc.return_value = allocation
		get_loads.return_value = [SimpleNamespace(name="DL-1"), SimpleNamespace(name="DL-2")]
		get_note_status.side_effect = [
			{
				"ready": True,
				"draft_delivery_notes": [],
				"submitted_delivery_notes": ["DN-1"],
			},
			{
				"ready": False,
				"draft_delivery_notes": ["DN-2"],
				"submitted_delivery_notes": [],
			},
		]

		status = get_allocation_delivery_note_status("VA-1")

		self.assertFalse(status["ready_for_delivery_trip"])
		self.assertEqual(status["draft_delivery_notes"], ["DN-2"])
		self.assertEqual(status["submitted_delivery_notes"], ["DN-1"])
		require_permission.assert_called_once_with(allocation, "read")

	@patch("vuna_transport.services.fleet.vehicle_allocation_service.get_delivery_note_submission_status")
	@patch("vuna_transport.services.fleet.vehicle_allocation_service.get_allocation_loads")
	@patch("vuna_transport.services.fleet.vehicle_allocation_service.require_document_permission")
	@patch("vuna_transport.services.fleet.vehicle_allocation_service.frappe.get_doc")
	@patch("vuna_transport.services.fleet.vehicle_allocation_service.frappe.db.get_value")
	def test_delivery_trip_action_is_not_offered_when_trip_already_exists(
		self, get_value, get_doc, require_permission, get_loads, get_note_status
	):
		allocation = SimpleNamespace(name="VA-1")
		get_doc.return_value = allocation
		get_value.return_value = "DT-1"
		get_loads.return_value = [SimpleNamespace(name="DL-1")]
		get_note_status.return_value = {
			"ready": True,
			"draft_delivery_notes": [],
			"submitted_delivery_notes": ["DN-1"],
		}

		status = get_allocation_delivery_note_status("VA-1")

		self.assertEqual(status["active_delivery_trip"], "DT-1")
		self.assertFalse(status["ready_for_delivery_trip"])

	def test_child_table_is_the_canonical_delivery_load_set(self):
		allocation = SimpleNamespace(
			delivery_loads=[
				SimpleNamespace(delivery_load="DL-001"),
				SimpleNamespace(delivery_load="DL-002"),
				SimpleNamespace(delivery_load="DL-001"),
			],
		)
		self.assertEqual(get_allocation_load_names(allocation), ["DL-001", "DL-002"])

	@patch(
		"vuna_transport.services.integrations.erpnext_integration.create_delivery_notes_for_allocation",
		return_value=["DN-001", "DN-002", "DN-003"],
	)
	@patch(
		"vuna_transport.vuna_transport.doctype.vehicle_allocation.vehicle_allocation.get_allocation_load_names",
		return_value=["DL-001", "DL-002"],
	)
	def test_create_delivery_notes_consolidates_at_allocation_scope(self, _get_load_names, create_notes):
		allocation = VehicleAllocation({"doctype": "Vehicle Allocation", "docstatus": 1})
		allocation.check_permission = MagicMock()

		created = allocation.create_delivery_notes()

		self.assertEqual(created, ["DN-001", "DN-002", "DN-003"])
		create_notes.assert_called_once_with(allocation)

	@patch(
		"vuna_transport.vuna_transport.doctype.vehicle_allocation.vehicle_allocation.frappe.db.get_single_value",
		return_value=0,
	)
	def test_cost_snapshot_is_skipped_when_costing_is_disabled(self, _get_single_value):
		allocation = SimpleNamespace(
			vehicle="KDA 123A", delivery_loads=[SimpleNamespace(delivery_load="DL-1")]
		)

		snapshot_transport_cost(allocation)

		self.assertFalse(hasattr(allocation, "estimated_marginal_cost"))

	@patch(
		"vuna_transport.vuna_transport.doctype.vehicle_allocation.vehicle_allocation.estimate_transport_cost_for_loads",
		return_value={
			"cost_structure_assignment": "Mileage 2026",
			"cost_structure": "Mileage",
			"distance": 300,
			"distance_uom": "Kilometer",
			"distance_source": "Planned Distance",
			"marginal_cost": 11100,
			"fixed_cost": 0,
			"estimated_full_cost": 11100,
			"currency": "KES",
			"components": [{"component": "Fuel", "amount": 11100}],
			"warnings": [],
		},
	)
	@patch(
		"vuna_transport.vuna_transport.doctype.vehicle_allocation.vehicle_allocation.frappe.db.get_single_value",
		return_value=1,
	)
	def test_cost_snapshot_is_captured_on_submission(self, _get_single_value, estimate):
		allocation = SimpleNamespace(
			vehicle="KDA 123A", delivery_loads=[SimpleNamespace(delivery_load="DL-1")]
		)

		snapshot_transport_cost(allocation)

		self.assertEqual(allocation.cost_structure_assignment, "Mileage 2026")
		self.assertEqual(allocation.estimated_marginal_cost, 11100)
		self.assertEqual(allocation.estimated_cost_currency, "KES")
		self.assertEqual(allocation.cost_breakdown[0]["component"], "Fuel")

	def test_overlapping_time_windows(self):
		first = SimpleNamespace(
			planned_departure="2026-08-15 08:00:00", planned_arrival="2026-08-15 12:00:00"
		)
		second = SimpleNamespace(
			planned_departure="2026-08-15 11:00:00", planned_arrival="2026-08-15 14:00:00"
		)
		self.assertTrue(time_windows_overlap(first, second))

	def test_non_overlapping_time_windows(self):
		first = SimpleNamespace(
			planned_departure="2026-08-15 08:00:00", planned_arrival="2026-08-15 10:00:00"
		)
		second = SimpleNamespace(
			planned_departure="2026-08-15 10:00:00", planned_arrival="2026-08-15 14:00:00"
		)
		self.assertFalse(time_windows_overlap(first, second))

	def test_vehicle_allocation_allows_date_only_planning(self):
		load = SimpleNamespace(
			name="LOAD-1",
			planned_departure=None,
			planned_arrival=None,
			dispatch_date="2026-08-24",
		)
		_validate_planning_window(load)
		from_time, to_time = get_load_resource_window(load)
		self.assertLess(from_time, to_time)

	def test_vehicle_allocation_rejects_partial_planning_window(self):
		load = SimpleNamespace(
			name="LOAD-1",
			planned_departure="2026-08-24 08:00:00",
			planned_arrival=None,
			dispatch_date="2026-08-24",
		)
		with self.assertRaises(ValidationError):
			_validate_planning_window(load)

	def test_vehicle_allocation_rejects_invalid_planning_window(self):
		load = SimpleNamespace(
			name="LOAD-1",
			planned_departure="2026-08-16 12:00:00",
			planned_arrival="2026-08-16 10:00:00",
		)
		with self.assertRaises(ValidationError):
			_validate_planning_window(load)

	@patch(
		"vuna_transport.services.fleet.vehicle_allocation_service.allocation_is_required_before_delivery_notes",
		return_value=False,
	)
	@patch("vuna_transport.services.fleet.vehicle_allocation_service.frappe.get_all", return_value=[])
	def test_after_notes_policy_rejects_initial_allocation_without_notes(self, _get_all, _policy):
		load = SimpleNamespace(
			name="LOAD-1",
			active_vehicle_allocation=None,
			delivery_method=None,
			delivery_orders=[SimpleNamespace(delivery_order="DO-1")],
		)
		with self.assertRaises(ValidationError):
			_validate_initial_allocation_sequence(load)

	def test_replacement_stage_tracks_actual_trip_execution(self):
		self.assertEqual(
			_get_replacement_stage(SimpleNamespace(status="Scheduled")),
			"Before Departure",
		)
		self.assertEqual(
			_get_replacement_stage(SimpleNamespace(status="In Transit")),
			"In Transit",
		)

	def test_subcontractor_requires_a_physical_vehicle_record(self):
		allocation = SimpleNamespace(vehicle=None)
		load = SimpleNamespace(name="LOAD-1")
		with self.assertRaises(ValidationError):
			_validate_subcontractor(allocation, load)

	@patch("vuna_transport.services.fleet.vehicle_allocation_service._validate_vehicle_is_available")
	@patch("vuna_transport.services.fleet.vehicle_allocation_service._validate_driver_is_available")
	@patch("vuna_transport.services.fleet.vehicle_allocation_service._validate_vehicle_category_for_load")
	@patch("vuna_transport.services.fleet.vehicle_allocation_service.lock_document")
	@patch("vuna_transport.services.fleet.vehicle_allocation_service.frappe.get_doc")
	def test_subcontractor_vehicle_populates_supplier_server_side(
		self, get_doc, lock_document, validate_category, validate_driver, validate_vehicle
	):
		allocation = SimpleNamespace(
			vehicle="KDA 123A",
			supplier=None,
			driver="DR-1",
		)
		load = SimpleNamespace(name="LOAD-1")
		get_doc.return_value = SimpleNamespace(
			vuna_vehicle_category="8 Ton Truck",
			vuna_supplier="ABC Logistics",
		)

		_validate_subcontractor(allocation, load)

		self.assertEqual(allocation.supplier, "ABC Logistics")

	@patch("vuna_transport.services.fleet.vehicle_allocation_service.frappe.get_doc")
	def test_cancellation_detaches_active_allocation_from_load(self, get_doc):
		load = SimpleNamespace(active_vehicle_allocation="VA-1", db_set=MagicMock())
		get_doc.return_value = load
		prepare_allocation_cancellation(
			SimpleNamespace(name="VA-1", delivery_loads=[SimpleNamespace(delivery_load="LOAD-1")])
		)
		load.db_set.assert_called_once_with("active_vehicle_allocation", None, update_modified=True)
