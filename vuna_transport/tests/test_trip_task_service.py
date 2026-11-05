import unittest
from unittest.mock import patch

import frappe

from vuna_transport.services.execution.trip_task_service import (
	group_stops,
	guard_task_source_cancellation,
	map_tasks_to_stops,
	plan_tasks,
	require_tasks_complete,
	standard_stop_complete,
)


def transfer_specs(origin="Main", destination="Depot", load="TL-1", demand="TD-1"):
	return [
		{
			"task_key": f"{kind}:{load}:{demand}",
			"task_type": kind,
			"transport_load": load,
			"transport_demand": demand,
			"address": address,
			"delivery_note": None,
		}
		for kind, address in (("Transfer Receipt", destination),)
	]


class TestTripTaskPlanning(unittest.TestCase):
	def test_standard_stop_requires_delivery_and_direct_pickup(self):
		stop = frappe._dict(delivery_note="DN-1", visited=0)
		pending = [frappe._dict(status="Ready")]
		complete = [frappe._dict(status="Completed")]
		self.assertFalse(standard_stop_complete(stop, pending, True))
		self.assertFalse(standard_stop_complete(stop, complete, False))
		self.assertTrue(standard_stop_complete(stop, complete, True))

	def test_pickup_only_stop_does_not_require_a_fake_delivery_note(self):
		self.assertTrue(
			standard_stop_complete(
				frappe._dict(delivery_note=None, visited=0), [frappe._dict(status="Completed")], False
			)
		)

	def test_return_pickup_and_receipt_preserve_order_and_real_visits(self):
		specs = [
			{
				"task_key": "PICK",
				"task_type": "Return Pickup",
				"transport_load": "RETURN-LOAD",
				"transport_demand": "RETURN-DEMAND",
				"address": "Depot",
				"delivery_note": None,
			},
			{
				"task_key": "RECEIVE",
				"task_type": "Transfer Receipt",
				"transport_load": "RETURN-LOAD",
				"transport_demand": "RETURN-DEMAND",
				"address": "Factory",
				"delivery_note": None,
			},
		]
		stops = self.stops(specs)
		self.assertEqual([stop.address for stop in stops], ["Depot", "Factory"])
		self.assertEqual(len(map_tasks_to_stops(specs, stops)), 2)
		stops[0].idx, stops[1].idx = 2, 1
		with self.assertRaises(ValueError):
			map_tasks_to_stops(specs, stops)

	def test_bundled_return_requests_share_pickup_and_receiving_visits(self):
		specs = [
			{
				"task_key": f"{kind}:{demand}",
				"task_type": kind,
				"transport_load": "RETURN-LOAD",
				"transport_demand": demand,
				"address": address,
				"delivery_note": None,
			}
			for demand in ("RR-DEMAND-1", "RR-DEMAND-2")
			for kind, address in (("Return Pickup", "Depot"), ("Transfer Receipt", "Factory"))
		]
		stops = self.stops(specs)
		self.assertEqual([stop.address for stop in stops], ["Depot", "Factory"])
		self.assertEqual(len(map_tasks_to_stops(specs, stops)), 4)

	def setUp(self):
		self.framework = self.enterContext(
			patch("vuna_transport.services.execution.trip_task_service.frappe")
		)
		self.framework.as_json.side_effect = frappe.as_json
		self.framework.parse_json.side_effect = frappe.parse_json
		self.framework.throw.side_effect = ValueError
		self.enterContext(
			patch("vuna_transport.services.execution.trip_task_service._", side_effect=lambda value: value)
		)

	def stops(self, specs):
		return [
			frappe._dict(
				name=f"STOP-{index}",
				idx=index + 1,
				**{key: value for key, value in stop.items() if key != "tasks"},
			)
			for index, stop in enumerate(group_stops(specs))
		]

	def test_transfer_needs_no_delivery_note(self):
		specs = transfer_specs()
		stops = self.stops(specs)
		self.assertEqual(len(stops), 1)
		self.assertEqual(stops[0].address, "Depot")
		self.assertEqual(len(map_tasks_to_stops(specs, stops)), 1)

	def test_planner_requests_only_destination_address_not_loading_address(self):
		item = frappe._dict(
			name="ITEM-1", item_code="MILK", transaction_uom="Carton", conversion_factor=10, stock_uom="Pcs"
		)
		demand = unittest.mock.MagicMock(
			docstatus=1,
			movement_type="Warehouse Transfer",
			company="Company",
			source_warehouse="Main",
			destination_warehouse="Depot",
			items=[item],
		)
		load = unittest.mock.MagicMock(
			company="Company",
			source_warehouse="Main",
			delivery_orders=[
				frappe._dict(
					transport_demand="TD-1",
					transport_demand_item="ITEM-1",
					item_code="MILK",
					allocated_quantity=2,
					allocated_stock_qty=20,
				)
			],
		)
		load.name = "TL-1"
		self.framework.get_doc.return_value = demand
		with (
			patch("vuna_transport.services.execution.trip_task_service.require_document_permission"),
			patch(
				"vuna_transport.services.execution.trip_task_service.get_warehouse_planning_data",
				return_value={"address": "Depot Address"},
			) as location,
		):
			specs = plan_tasks([load])
		location.assert_called_once_with("Depot")
		self.assertEqual([spec["task_type"] for spec in specs], ["Transfer Receipt"])
		self.assertEqual(specs[0]["items"][0]["qty"], 2)

	def test_mixed_trip_has_customer_and_depot_stops_but_no_loading_stop(self):
		specs = [
			*transfer_specs(),
			{
				"task_key": "DN-1",
				"task_type": "Customer Delivery",
				"delivery_note": "DN-1",
				"transport_load": "TL-2",
				"address": "Customer",
				"customer": "Customer",
			},
		]
		stops = self.stops(specs)
		self.assertEqual({stop.address for stop in stops}, {"Customer", "Depot"})
		self.assertEqual(len(stops), 2)

	def test_multiple_transfer_loads_to_one_location_have_one_stop(self):
		self.assertEqual(len(self.stops(transfer_specs() + transfer_specs(load="TL-2", demand="TD-2"))), 1)

	def test_customer_and_transfer_share_stop_without_sharing_pod(self):
		specs = [
			*transfer_specs(),
			{
				"task_key": "DN-1",
				"task_type": "Customer Delivery",
				"delivery_note": "DN-1",
				"transport_load": "TL-2",
				"address": "Depot",
				"customer": "Customer",
			},
		]
		stops = self.stops(specs)
		self.assertEqual(len(stops), 1)
		self.assertEqual(len(frappe.parse_json(stops[0].vuna_task_keys)), 2)
		self.assertEqual(len(map_tasks_to_stops(specs, stops)), 2)

	def test_preloaded_transfers_generate_only_their_receiving_destinations(self):
		specs = transfer_specs() + transfer_specs("Depot", "Main", "TL-2", "TD-2")
		stops = self.stops(specs)
		self.assertEqual([stop.address for stop in stops], ["Depot", "Main"])
		self.assertEqual(len(map_tasks_to_stops(specs, stops)), 2)

	def test_receiving_destinations_can_be_reordered(self):
		specs = transfer_specs() + transfer_specs(destination="Other", load="TL-2", demand="TD-2")
		stops = self.stops(specs)
		stops[0].idx, stops[1].idx = 2, 1
		self.assertEqual(len(map_tasks_to_stops(specs, stops)), 2)

	def test_extra_delivery_note_is_rejected(self):
		specs = transfer_specs()
		stops = self.stops(specs)
		stops[0].delivery_note = "FOREIGN-DN"
		with self.assertRaises(ValueError):
			map_tasks_to_stops(specs, stops)

	def test_missing_or_duplicate_task_assignment_is_rejected(self):
		specs = transfer_specs() + transfer_specs(destination="Other", load="TL-2", demand="TD-2")
		stops = self.stops(specs)
		stops[1].vuna_task_keys = stops[0].vuna_task_keys
		with self.assertRaises(ValueError):
			map_tasks_to_stops(specs, stops)

	def test_visited_flags_cannot_bypass_pending_tasks(self):
		self.framework.db.exists.return_value = True
		with self.assertRaises(ValueError):
			require_tasks_complete(frappe._dict(name="TRIP-1", vuna_task_based=1))

	def test_active_tasks_block_source_cancellation(self):
		self.framework.db.exists.return_value = True
		with self.assertRaises(ValueError):
			guard_task_source_cancellation(frappe._dict(doctype="Transport Load", name="TL-1"))

	def test_cancelled_tasks_do_not_trap_source_documents(self):
		self.framework.db.exists.return_value = False
		doc = frappe._dict(doctype="Delivery Note", name="DN-1", ignore_linked_doctypes=["Existing"])
		guard_task_source_cancellation(doc)
		self.assertEqual(set(doc.ignore_linked_doctypes), {"Existing", "Transport Trip Task"})
