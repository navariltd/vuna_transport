import unittest
from unittest.mock import patch

import frappe

from vuna_transport.services.execution.trip_task_service import (
	group_stops,
	guard_task_source_cancellation,
	map_tasks_to_stops,
	require_tasks_complete,
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
		for kind, address in (("Transfer Dispatch", origin), ("Transfer Receipt", destination))
	]


class TestTripTaskPlanning(unittest.TestCase):
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
		self.assertEqual(len(stops), 2)
		self.assertEqual(len(map_tasks_to_stops(specs, stops)), 2)

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
		self.assertEqual(len(stops), 2)
		self.assertEqual(len(frappe.parse_json(stops[1].vuna_task_keys)), 2)
		self.assertEqual(len(map_tasks_to_stops(specs, stops)), 3)

	def test_round_trip_keeps_repeat_visit_to_origin(self):
		specs = transfer_specs() + transfer_specs("Depot", "Main", "TL-2", "TD-2")
		stops = self.stops(specs)
		self.assertEqual([stop.address for stop in stops], ["Main", "Depot", "Main"])
		self.assertEqual(len(map_tasks_to_stops(specs, stops)), 4)

	def test_receipt_before_dispatch_is_rejected(self):
		specs = transfer_specs()
		stops = self.stops(specs)
		stops[0].idx, stops[1].idx = 2, 1
		with self.assertRaises(ValueError):
			map_tasks_to_stops(specs, stops)

	def test_extra_delivery_note_is_rejected(self):
		specs = transfer_specs()
		stops = self.stops(specs)
		stops[0].delivery_note = "FOREIGN-DN"
		with self.assertRaises(ValueError):
			map_tasks_to_stops(specs, stops)

	def test_missing_or_duplicate_task_assignment_is_rejected(self):
		specs = transfer_specs()
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
