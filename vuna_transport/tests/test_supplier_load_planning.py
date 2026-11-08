"""Planned inbound loads retain PO/UOM identity without pre-pickup inventory."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import frappe

from vuna_transport import api
from vuna_transport.services.planning import supplier_load_service as service


def document(**values):
	obj = SimpleNamespace(**values)
	obj.get = lambda key: getattr(obj, key, None)
	obj.reload = lambda: None
	obj.check_permission = lambda *args: None
	obj.update = lambda values: obj.__dict__.update(values)
	return obj


def fail(message):
	raise ValueError(message)


class TestSupplierLoadPlanning(TestCase):
	def setUp(self):
		self.f = self.enterContext(patch.object(service, "frappe"))
		self.f.throw.side_effect = fail
		self.f.get_cached_value.return_value = 0
		self.enterContext(patch.object(service, "_", side_effect=lambda value: value))
		self.item = frappe._dict(
			name="DI",
			source_item="POI",
			item_code="PACK",
			stock_qty=20,
			conversion_factor=10,
			transaction_uom="Carton",
			stock_uom="Pcs",
		)
		self.demand = document(
			name="TD",
			company="Company",
			docstatus=1,
			movement_type="Supplier Pickup",
			source_document_type="Purchase Order",
			source_document="PO",
			source_address="SUPPLIER-ADDRESS",
			destination_warehouse="Depot",
			status="Open",
			items=[self.item],
		)
		self.order = document(supplier="Supplier", vuna_delivery_method="Planned Collection")
		self.context = self.enterContext(
			patch.object(
				service,
				"source_context",
				return_value=(
					self.order,
					{
						"POI": {
							"item_code": "PACK",
							"transaction_uom": "Carton",
							"stock_uom": "Pcs",
							"conversion_factor": 10,
							"stock_qty": 20,
						}
					},
				),
			)
		)
		self.balances = self.enterContext(patch.object(service, "balances", return_value=({}, {})))
		self.row = frappe._dict(
			name="LI", transport_demand="TD", transport_demand_item="DI", allocated_quantity=2
		)
		self.row.set = lambda key, value: self.row.__setitem__(key, value)
		self.row.precision = lambda field: 6
		self.load = document(
			name="TL", company="Company", source_warehouse=None, delivery_orders=[self.row], docstatus=1
		)
		self.f.get_doc.return_value = self.demand
		self.enterContext(
			patch(
				"vuna_transport.services.planning.load_validation_service.validate_transport_compatibility_groups"
			)
		)
		self.enterContext(
			patch(
				"vuna_transport.services.demand.delivery_order_service._get_group_transport_requirements",
				return_value={"refrigeration_required": 1},
			)
		)
		self.enterContext(
			patch(
				"vuna_transport.services.demand.delivery_order_service._get_item_transport_measurements",
				return_value=frappe._dict(weight=4, volume=2),
			)
		)
		self.enterContext(
			patch(
				"vuna_transport.services.shared.measurement_service.get_transport_uoms",
				return_value=frappe._dict(weight_uom="Kg", volume_uom="Cubic Meter"),
			)
		)

	def test_external_pickup_is_not_forced_into_a_company_warehouse(self):
		service.validate_supplier_allocations(self.load)
		self.assertEqual(
			(self.load.source_address, self.load.supplier, self.load.movement_type),
			("SUPPLIER-ADDRESS", "Supplier", "Supplier Pickup"),
		)
		self.assertIsNone(self.load.source_warehouse)
		self.assertEqual(
			(self.row.uom, self.row.allocated_quantity, self.row.allocated_stock_qty), ("Carton", 2, 20)
		)
		self.assertEqual(
			(self.load.total_weight, self.load.total_volume, self.load.refrigeration_required), (4, 2, 1)
		)

	def test_source_warehouse_and_wrong_company_are_rejected(self):
		for field, value in (("source_warehouse", "Main"), ("company", "Other")):
			with patch.object(self.load, field, value), self.assertRaises(ValueError):
				service.validate_supplier_allocations(self.load)

	def test_overallocation_duplicates_nan_and_negative_quantities_are_rejected(self):
		for qty in (3, 0, -1, float("nan"), float("inf")):
			self.row.allocated_quantity = qty
			with self.assertRaises(ValueError):
				service.validate_supplier_allocations(self.load)
		self.row.allocated_quantity = 1
		self.load.delivery_orders.append(self.row)
		with self.assertRaises(ValueError):
			service.validate_supplier_allocations(self.load)

	def test_po_receipts_and_allocations_do_not_subtract_collected_goods_twice(self):
		self.context.return_value[1]["POI"]["stock_qty"] = 10
		self.balances.return_value = ({"DI": 10}, {"DI": 10})
		self.assertEqual(service.available_items(self.demand)[0]["available_quantity"], 1)

	def test_other_active_loads_remain_reserved(self):
		self.balances.return_value = ({"DI": 10}, {})
		self.assertEqual(service.available_items(self.demand)[0]["available_quantity"], 1)

	def test_changed_uom_or_item_lineage_is_rejected(self):
		self.item.transaction_uom = "Pcs"
		with self.assertRaises(ValueError):
			service.available_items(self.demand)

	def test_planned_supplier_task_has_only_the_supplier_pickup_stop(self):
		service.validate_supplier_allocations(self.load)
		rows = service.supplier_tasks(self.load)
		self.assertEqual(len(rows), 1)
		self.assertEqual((rows[0]["task_type"], rows[0]["address"]), ("Supplier Pickup", "SUPPLIER-ADDRESS"))
		self.assertEqual(rows[0]["items"][0]["load_item"], "LI")
		self.assertEqual(rows[0]["items"][0]["uom"], "Carton")

	def test_task_creation_rejects_a_different_pickup_address(self):
		service.validate_supplier_allocations(self.load)
		self.load.source_address = "Other address"
		with self.assertRaises(ValueError):
			service.supplier_tasks(self.load)


class TestSupplierPlanningPolicy(TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(service, "frappe"))
		self.framework.throw.side_effect = fail
		self.enterContext(patch.object(service, "require_company_access"))
		self.enterContext(
			patch("vuna_transport.services.demand.source_policy_service.require_source_enabled")
		)
		self.workflow = self.enterContext(
			patch(
				"vuna_transport.services.demand.delivery_method_service.get_delivery_method_workflow",
				return_value=frappe._dict(disabled=False, requires_transport_planning=True),
			)
		)
		self.demand = document(
			name="TD",
			company="Company",
			docstatus=1,
			status="Open",
			movement_type="Supplier Pickup",
			source_document_type="Purchase Order",
			source_document="PO",
			source_key="KEY",
			source_address="Supplier Address",
			destination_warehouse="Main",
		)
		self.order = document(vuna_delivery_method="Collection")
		self.framework.get_doc.return_value = self.order
		self.legs = self.enterContext(
			patch(
				"vuna_transport.services.demand.supplier_pickup_demand_service.build_supplier_pickup_legs",
				return_value=[
					{
						"source_key": "KEY",
						"company": "Company",
						"source_address": "Supplier Address",
						"destination_warehouse": "Main",
						"items": [{"source_item": "POI"}],
					}
				],
			)
		)

	def test_planned_method_uses_the_live_purchase_order_leg(self):
		order, rows = service.source_context(self.demand)
		self.assertIs(order, self.order)
		self.assertIn("POI", rows)
		self.framework.get_doc.assert_called_once_with("Purchase Order", "PO", for_update=True)

	def test_manual_or_disabled_method_cannot_bypass_load_policy(self):
		for disabled, planning in ((False, False), (True, True)):
			self.workflow.return_value = frappe._dict(disabled=disabled, requires_transport_planning=planning)
			with self.assertRaisesRegex(ValueError, "does not use load planning"):
				service.source_context(self.demand)

	def test_changed_destination_and_archived_demands_cannot_create_work(self):
		self.demand.destination_warehouse = "Other"
		with self.assertRaisesRegex(ValueError, "no longer matches"):
			service.source_context(self.demand)
		self.demand.migration_archived = 1
		with self.assertRaisesRegex(ValueError, "Historical-only"):
			service.source_context(self.demand)


class TestNativeAllocationPicker(TestCase):
	def test_cancelling_supplier_allocation_returns_load_to_awaiting_vehicle(self):
		from vuna_transport.services.fleet import vehicle_allocation_service as allocations

		load = document(name="TL", movement_type="Supplier Pickup", status="Ready for Dispatch")
		load.db_set = lambda *args, **kwargs: None
		allocation = document(name="VA", replacement_of=None)
		with (
			patch.object(allocations, "get_allocation_loads", return_value=[load]),
			patch.object(allocations, "frappe") as framework,
			patch.object(allocations, "set_delivery_load_status") as transition,
		):
			framework.db.exists.return_value = False
			allocations.restore_previous_allocation(allocation, active_link_cleared=True)
			self.assertEqual(transition.call_args.args[:2], (load, "Awaiting Vehicle"))

	def test_no_candidates_does_not_query_legacy_documents(self):
		with patch.object(api, "frappe") as framework:
			self.assertEqual(api._loads_at_vehicle_allocation_step([]), [])
			framework.get_all.assert_not_called()

	def test_customer_readiness_uses_the_shared_native_quantity_check(self):
		candidates = [
			frappe._dict(name=name, delivery_method="Company Delivery")
			for name in ("READY", "DRAFT", "PARTIAL")
		]
		with (
			patch.object(api, "frappe") as framework,
			patch.object(
				api, "get_vehicle_allocation_timing", return_value=api.ALLOCATION_AFTER_DELIVERY_NOTES
			),
			patch(
				"vuna_transport.services.planning.transport_load_service.is_demand_load", return_value=False
			),
			patch(
				"vuna_transport.services.fleet.vehicle_allocation_service.get_delivery_note_submission_status",
				side_effect=lambda load: {"ready": load.name == "READY"},
			) as check,
		):
			framework.get_all.side_effect = [
				[frappe._dict(name="Company Delivery", requires_vehicle_allocation=1)],
				[],
				[],
			]
			framework.get_doc.side_effect = lambda doctype, name: frappe._dict(name=name)
			self.assertEqual(
				[row.name for row in api._loads_at_vehicle_allocation_step(candidates)], ["READY"]
			)
			self.assertEqual(check.call_count, 3)

	def test_supplier_and_depot_loads_do_not_require_customer_delivery_notes(self):
		candidate = frappe._dict(name="SUPPLIER", delivery_method="Collection")
		with (
			patch.object(api, "frappe") as framework,
			patch.object(
				api, "get_vehicle_allocation_timing", return_value=api.ALLOCATION_AFTER_DELIVERY_NOTES
			),
			patch(
				"vuna_transport.services.planning.transport_load_service.is_demand_load", return_value=True
			),
			patch(
				"vuna_transport.services.fleet.vehicle_allocation_service.get_delivery_note_submission_status"
			) as notes,
		):
			framework.get_all.side_effect = [
				[frappe._dict(name="Collection", requires_vehicle_allocation=1)],
				[],
				[],
			]
			self.assertEqual(api._loads_at_vehicle_allocation_step([candidate]), [candidate])
			notes.assert_not_called()
