"""Manual trip assignment is explicit, bounded and does not post stock."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from vuna_transport.services.execution import direct_supplier_pickup_service as service


def document(**values):
	doc = SimpleNamespace(**values)
	doc.get = lambda key: getattr(doc, key, None)
	doc.reload = MagicMock()
	doc.add_comment = MagicMock()
	return doc


class TestDirectSupplierPickup(TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(service, "frappe"))
		self.framework.throw.side_effect = ValueError
		self.framework._dict.side_effect = frappe._dict
		self.enterContext(patch.object(service, "_", side_effect=lambda value: value))
		self.enterContext(patch.object(service, "require_document_permission"))
		self.item = frappe._dict(
			name="DI",
			source_item="POI",
			item_code="PACK",
			transaction_qty=2,
			transaction_uom="Carton",
			conversion_factor=10,
			stock_qty=20,
			stock_uom="Pcs",
		)
		self.demand = document(
			name="TD",
			docstatus=1,
			company="Company",
			movement_type="Supplier Pickup",
			source_document_type="Purchase Order",
			source_document="PO",
			source_key="LEG",
			source_address="Supplier Gate",
			destination_warehouse="Main",
			items=[self.item],
		)
		self.trip = document(
			name="DT",
			docstatus=1,
			company="Company",
			status="In Transit",
			vuna_actual_start="2026-11-07",
			vuna_actual_completion=None,
			vehicle="TRUCK",
			driver="DRIVER",
			vuna_current_vehicle=None,
			vuna_current_driver=None,
			vuna_current_vehicle_allocation=None,
			vuna_vehicle_allocation="VA",
			delivery_stops=[],
		)
		self.allocation = document(
			name="VA", docstatus=1, company="Company", vehicle="TRUCK", driver="DRIVER", status="Allocated"
		)
		self.order = document(name="PO", company="Company", vuna_delivery_method="Manual Collection")
		self.leg = dict(
			source_key="LEG",
			source_address="Supplier Gate",
			destination_warehouse="Main",
			company="Company",
			items=[dict(self.item, transaction_uom="Carton")],
		)
		self.documents = {
			"Transport Demand": self.demand,
			"Delivery Trip": self.trip,
			"Vehicle Allocation": self.allocation,
			"Purchase Order": self.order,
		}
		self.framework.get_doc.side_effect = lambda doctype, name: self.documents[doctype]
		self.enterContext(
			patch("vuna_transport.services.demand.source_policy_service.require_source_enabled")
		)
		self.enterContext(
			patch(
				"vuna_transport.services.execution.supplier_vehicle_receipt_service.reserved_purchase_quantities",
				return_value={},
			)
		)
		self.workflow = self.enterContext(
			patch(
				"vuna_transport.services.demand.delivery_method_service.get_delivery_method_workflow",
				return_value=frappe._dict(transport_arranged_by="Company", requires_transport_planning=False),
			)
		)
		self.legs = self.enterContext(
			patch(
				"vuna_transport.services.demand.supplier_pickup_demand_service.build_supplier_pickup_legs",
				return_value=[self.leg],
			)
		)

	def test_manual_company_collection_uses_existing_trip(self):
		self.assertIs(service.validate_assignment_context(self.demand, self.trip), self.order)

	def test_invalid_trip_or_company_does_not_get_assigned(self):
		for changes in (
			{"docstatus": 0},
			{"status": "Completed"},
			{"status": "Cancelled"},
			{"company": "Other"},
			{"driver": None},
			{"vehicle": None},
			{"vuna_actual_completion": "done"},
			{"vuna_vehicle_allocation": None},
		):
			with patch.multiple(self.trip, **changes), self.assertRaises(ValueError):
				service.validate_assignment_context(self.demand, self.trip)

	def test_wrong_source_or_unsubmitted_demand_is_rejected(self):
		for changes in (
			{"docstatus": 0},
			{"movement_type": "Warehouse Transfer"},
			{"source_document_type": "Sales Order"},
		):
			with patch.multiple(self.demand, **changes), self.assertRaises(ValueError):
				service.validate_assignment_context(self.demand, self.trip)

	def test_mismatched_current_allocation_is_rejected(self):
		with patch.object(self.allocation, "vehicle", "OTHER"), self.assertRaises(ValueError):
			service.validate_assignment_context(self.demand, self.trip)

	def test_planning_methods_cannot_silently_bypass_required_workflow(self):
		self.workflow.return_value.requires_transport_planning = True
		with self.assertRaises(ValueError):
			service.validate_assignment_context(self.demand, self.trip)

	def test_source_uom_endpoints_and_outstanding_quantity_are_revalidated(self):
		for changes in (
			{"transaction_qty": 3},
			{"conversion_factor": 1},
			{"item_code": "OTHER"},
			{"transaction_uom": "Pcs"},
		):
			with patch.dict(self.item, changes), self.assertRaises(ValueError):
				service.validate_assignment_context(self.demand, self.trip)
		with patch.object(self.demand, "destination_warehouse", "OTHER"), self.assertRaises(ValueError):
			service.validate_assignment_context(self.demand, self.trip)

	def test_drivers_cannot_assign_themselves_cargo(self):
		self.framework.get_roles.return_value = ["Driver"]
		with self.assertRaises(ValueError):
			service.require_dispatcher(self.demand, self.trip)

	def test_retry_returns_same_task_and_does_not_repeat_capacity_or_stop_creation(self):
		self.framework.db.get_value.return_value = frappe._dict(name="TASK", delivery_trip="DT")
		with (
			patch.object(service, "require_dispatcher"),
			patch.object(service, "validate_trip_capacity") as capacity,
		):
			self.assertEqual(service.assign_supplier_demand("TD", "DT"), "TASK")
			capacity.assert_not_called()

	def test_one_demand_cannot_be_assigned_to_two_trips(self):
		self.framework.db.get_value.return_value = frappe._dict(name="TASK", delivery_trip="OTHER")
		with patch.object(service, "require_dispatcher"), self.assertRaises(ValueError):
			service.assign_supplier_demand("TD", "DT")

	def test_capacity_failure_happens_before_stop_or_task_writes(self):
		self.framework.db.get_value.return_value = None
		self.framework.db.sql.return_value = []
		with (
			patch.object(service, "require_dispatcher"),
			patch.object(service, "validate_trip_capacity", side_effect=ValueError),
			self.assertRaises(ValueError),
		):
			service.assign_supplier_demand("TD", "DT")
		self.assertFalse(
			any(isinstance(call.args[0], dict) for call in self.framework.get_doc.call_args_list)
		)

	def test_receipt_limits_preserve_planned_transaction_uom(self):
		task = document(
			direct_assignment=1,
			task_type="Supplier Pickup",
			status="Ready",
			delivery_trip="DT",
			transport_demand="TD",
			items=[
				frappe._dict(
					transport_demand_item="DI", item_code="PACK", qty=2, uom="Carton", conversion_factor=10
				)
			],
		)
		self.assertEqual(service.receipt_task_limits(task, self.order, self.allocation), {"POI": 2})
		task.items[0].uom = "Pcs"
		with self.assertRaises(ValueError):
			service.receipt_task_limits(task, self.order, self.allocation)

	def test_receipt_requires_trip_start_and_current_allocation(self):
		task = document(
			direct_assignment=1,
			task_type="Supplier Pickup",
			status="Ready",
			delivery_trip="DT",
			transport_demand="TD",
			items=[],
		)
		with patch.object(self.trip, "vuna_actual_start", None), self.assertRaises(ValueError):
			service.receipt_task_limits(task, self.order, self.allocation)
		with patch.object(self.trip, "vuna_vehicle_allocation", "OTHER"), self.assertRaises(ValueError):
			service.receipt_task_limits(task, self.order, self.allocation)

	def test_draft_and_submitted_receipt_determine_task_status(self):
		task = document(
			name="TASK", transport_demand="TD", items=[frappe._dict(transport_demand_item="DI", qty=2)]
		)
		self.framework.db.get_value.return_value = None
		self.assertEqual(service.supplier_task_status(task), "Ready")
		self.framework.db.get_value.return_value = frappe._dict(name="PR", docstatus=0)
		self.assertEqual(service.supplier_task_status(task), "Awaiting Stock Posting")
		self.framework.db.get_value.return_value.docstatus = 1
		self.documents["Purchase Receipt"] = document(items=[frappe._dict(purchase_order_item="POI", qty=2)])
		self.assertEqual(service.supplier_task_status(task), "Completed")
		self.documents["Purchase Receipt"].items[0].qty = 1
		self.assertEqual(service.supplier_task_status(task), "Completed with Variance")
