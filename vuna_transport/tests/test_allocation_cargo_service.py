import unittest
from unittest.mock import MagicMock, patch

import frappe

from vuna_transport.services.fleet.allocation_cargo_service import collect_load_items, get_allocation_items


def run_site_smoke():
	"""Read real load rows without creating or changing any allocation."""
	names = frappe.get_all("Transport Load", filters={"docstatus": 1}, pluck="name", limit_page_length=3)
	rows = get_allocation_items(names)
	field = frappe.get_meta("Vehicle Allocation").get_field("loaded_items")
	assert field.read_only and field.options == "Vehicle Allocation Item"
	assert all(row["transport_load"] in names for row in rows)
	allocation = frappe.new_doc("Vehicle Allocation")
	allocation.set("loaded_items", rows)
	assert len(allocation.loaded_items) == len(rows)
	return {"loads_checked": len(names), "item_rows": len(rows), "read_only_table": True, "writes": 0}


class TestAllocationItems(unittest.TestCase):
	def setUp(self):
		self.framework = self.enterContext(
			patch("vuna_transport.services.fleet.allocation_cargo_service.frappe")
		)
		self.enterContext(
			patch("vuna_transport.services.fleet.allocation_cargo_service._", side_effect=lambda value: value)
		)
		self.framework.throw.side_effect = ValueError
		self.permissions = self.enterContext(
			patch("vuna_transport.services.fleet.allocation_cargo_service.require_document_permission")
		)
		self.source_item = frappe._dict(
			name="ITEM-1", item_code="MILK", uom="Carton", transaction_uom="Carton", stock_uom="Pcs"
		)
		self.framework.get_doc.return_value = MagicMock(items=[self.source_item])

	def load(self, name="TL-1", transfer=False, qty=2, weight=10):
		row = frappe._dict(
			item_code="MILK",
			item_name="Milk",
			allocated_quantity=qty,
			weight=weight,
			weight_uom="Kg",
			delivery_order=None if transfer else "DO-1",
			delivery_order_item="ITEM-1",
			transport_demand="TD-1" if transfer else None,
			transport_demand_item="ITEM-1",
		)
		load = MagicMock(docstatus=1, delivery_orders=[row])
		load.name = name
		return load

	def test_customer_quantity_stays_in_transaction_uom(self):
		rows = collect_load_items([self.load()])
		self.assertEqual(rows[0]["qty"], 2)
		self.assertEqual(rows[0]["uom"], "Carton")
		self.assertEqual(rows[0]["transport_load"], "TL-1")

	def test_transfer_quantity_preserves_requested_uom(self):
		rows = collect_load_items([self.load(transfer=True)])
		self.assertEqual(rows[0]["uom"], "Carton")

	def test_same_item_in_different_loads_stays_separate(self):
		self.assertEqual(len(collect_load_items([self.load(), self.load("TL-2")])), 2)

	def test_matching_rows_are_summed_within_one_load(self):
		load = self.load()
		load.delivery_orders.append(frappe._dict(load.delivery_orders[0]))
		rows = collect_load_items([load])
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["qty"], 4)
		self.assertEqual(rows[0]["weight"], 20)

	def test_different_weight_units_are_not_summed(self):
		load = self.load()
		other = frappe._dict(load.delivery_orders[0])
		other.weight_uom = "Gram"
		load.delivery_orders.append(other)
		self.assertEqual(len(collect_load_items([load])), 2)

	def test_cancelled_load_is_rejected(self):
		load = self.load()
		load.docstatus = 2
		with self.assertRaises(ValueError):
			collect_load_items([load])

	def test_source_item_must_match_load_item(self):
		self.source_item.item_code = "OTHER"
		with self.assertRaises(ValueError):
			collect_load_items([self.load()])

	def test_validation_rebuilds_items_instead_of_trusting_client_quantities(self):
		from vuna_transport.vuna_transport.doctype.vehicle_allocation.vehicle_allocation import (
			VehicleAllocation,
		)

		allocation = MagicMock(loaded_items=[{"qty": 999999}])
		allocation.get.side_effect = {"route_stops": [], "route_status": "Unplanned"}.get
		allocation.get_doc_before_save.return_value = None
		with (
			patch(
				"vuna_transport.vuna_transport.doctype.vehicle_allocation.vehicle_allocation.validate_allocation"
			),
			patch(
				"vuna_transport.services.fleet.vehicle_allocation_service.get_allocation_loads",
				return_value=[self.load()],
			),
			patch(
				"vuna_transport.services.fleet.vehicle_incident_service.validate_replacement_allocation_incident"
			),
		):
			VehicleAllocation.validate(allocation)
		item_updates = [
			call.args[1] for call in allocation.set.call_args_list if call.args[0] == "loaded_items"
		]
		self.assertEqual(len(item_updates), 1)
		self.assertEqual(item_updates[0][0]["qty"], 2)
		self.assertEqual(allocation.route_status, "Unplanned")

	def test_load_permission_is_checked_before_source_access(self):
		self.permissions.side_effect = PermissionError
		with self.assertRaises(PermissionError):
			collect_load_items([self.load()])
		self.framework.get_doc.assert_not_called()
