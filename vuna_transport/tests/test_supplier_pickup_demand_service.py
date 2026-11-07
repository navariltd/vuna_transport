"""Supplier collection planning cannot create stock or a financial receipt."""

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from vuna_transport.services.demand import supplier_pickup_demand_service as service
from vuna_transport.services.demand.transport_demand_service import build_source_legs


def purchase_order():
	return frappe._dict(
		doctype="Purchase Order",
		name="PO-1",
		company="Company",
		supplier="Supplier",
		docstatus=1,
		status="To Receive and Bill",
		vuna_delivery_method="Company Pickup",
		vuna_pickup_address="Supplier Gate",
		items=[
			frappe._dict(
				name="POI-1",
				idx=1,
				item_code="PACK",
				qty=20,
				received_qty=5,
				stock_qty=200,
				uom="Carton",
				stock_uom="Pcs",
				conversion_factor=10,
				warehouse="Main",
				schedule_date="2026-11-10",
			)
		],
	)


class TestSupplierPickupDemand(TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(service, "frappe"))
		self.framework.throw.side_effect = ValueError
		self.enterContext(
			patch(
				"vuna_transport.services.demand.delivery_method_service.frappe.get_cached_doc",
				side_effect=lambda doctype, name: frappe._dict(
					name=name,
					buying=1,
					selling=0,
					transport_arranged_by="Company",
					disabled=0,
					requires_transport_planning=1,
					requires_vehicle_allocation=1,
					requires_delivery_trip=1,
				),
			)
		)
		self.enterContext(
			patch(
				"vuna_transport.services.demand.transport_demand_service.frappe.throw", side_effect=ValueError
			)
		)
		self.enterContext(patch.object(service, "_", side_effect=lambda value: value))
		self.address = frappe._dict(
			name="Supplier Gate",
			disabled=0,
			links=[frappe._dict(link_doctype="Supplier", link_name="Supplier")],
		)
		self.warehouse = frappe._dict(
			name="Main", company="Company", disabled=0, is_group=0, warehouse_type="Stores"
		)
		self.framework.get_doc.side_effect = (
			lambda doctype, name: self.address if doctype == "Address" else self.warehouse
		)
		self.framework.get_cached_value.return_value = 1
		self.source = purchase_order()

	def test_outstanding_po_received_quantity_is_in_transaction_uom(self):
		leg = build_source_legs(self.source)[0]
		self.assertEqual(leg["movement_type"], "Supplier Pickup")
		self.assertEqual(leg["items"][0]["transaction_qty"], 15)
		self.assertEqual(leg["items"][0]["stock_qty"], 150)
		self.assertEqual(leg["items"][0]["transaction_uom"], "Carton")
		self.assertIsNone(leg["source_warehouse"])
		self.assertIsNone(leg["customer"])
		self.assertEqual(leg["supplier"], "Supplier")
		self.assertEqual(leg["source_address"], "Supplier Gate")
		self.assertEqual(leg["destination_warehouse"], "Main")

	def test_supplier_warehouse_and_date_groups_have_stable_distinct_keys(self):
		self.source["items"].append(
			frappe._dict(**{**self.source["items"][0], "name": "POI-2", "warehouse": "Depot"})
		)
		self.source["items"].append(
			frappe._dict(**{**self.source["items"][0], "name": "POI-3", "schedule_date": "2026-11-11"})
		)
		legs = build_source_legs(self.source)
		self.assertEqual(len(legs), 3)
		self.assertEqual(len({leg["source_key"] for leg in legs}), 3)
		self.assertEqual(legs, build_source_legs(self.source))

	def test_unopted_draft_closed_on_hold_subcontracted_orders_are_rejected(self):
		for changes in (
			{"vuna_delivery_method": None},
			{"docstatus": 0},
			{"status": "Closed"},
			{"status": "On Hold"},
			{"is_subcontracted": 1},
		):
			with self.subTest(changes=changes), self.assertRaises(ValueError):
				build_source_legs(frappe._dict({**self.source, **changes}))

	def test_fully_received_drop_ship_and_nonstock_rows_do_not_generate_cargo(self):
		for changes in ({"received_qty": 20}, {"delivered_by_supplier": 1}):
			self.source["items"][0].update(changes)
			self.assertEqual(build_source_legs(self.source), [])
		self.source = purchase_order()
		self.framework.get_cached_value.return_value = 0
		self.assertEqual(build_source_legs(self.source), [])

	def test_disabled_or_unrelated_supplier_address_is_rejected(self):
		for changes in (
			{"disabled": 1},
			{"links": []},
			{"links": [frappe._dict(link_doctype="Supplier", link_name="Other")]},
		):
			with patch.dict(self.address, changes), self.assertRaises(ValueError):
				build_source_legs(self.source)

	def test_receiving_warehouse_checks_company_storage_and_active_state(self):
		for changes in (
			{"company": "Other"},
			{"disabled": 1},
			{"is_group": 1},
			{"warehouse_type": "Transit"},
		):
			with patch.dict(self.warehouse, changes), self.assertRaises(ValueError):
				build_source_legs(self.source)

	def test_missing_warehouse_can_use_standard_po_header_default(self):
		self.source["items"][0].warehouse = None
		with self.assertRaises(ValueError):
			build_source_legs(self.source)
		self.source.set_warehouse = "Main"
		self.assertEqual(build_source_legs(self.source)[0]["destination_warehouse"], "Main")

	def test_invalid_conversion_nonfinite_values_or_missing_date_are_rejected(self):
		for changes in (
			{"conversion_factor": 0},
			{"conversion_factor": float("nan")},
			{"qty": float("inf")},
			{"received_qty": float("nan")},
			{"schedule_date": None},
		):
			with patch.dict(self.source["items"][0], changes), self.assertRaises(ValueError):
				build_source_legs(self.source)

	def test_editing_pickup_identity_locks_source_and_requires_demand_release(self):
		previous = purchase_order()
		self.source.vuna_pickup_address = "New Gate"
		doc = MagicMock(name="PO-1", vuna_delivery_method="Company Pickup")
		doc.name = "PO-1"
		doc.get.side_effect = self.source.get
		doc.get_doc_before_save.return_value = previous
		self.framework.db.exists.return_value = True
		with self.assertRaises(ValueError):
			service.validate_supplier_pickup_order(doc)
		self.framework.db.get_value.assert_called_once_with("Purchase Order", "PO-1", "name", for_update=True)

	def test_normal_purchase_order_has_no_transport_side_effect(self):
		doc = MagicMock()
		doc.get_doc_before_save.return_value = None
		doc.get.return_value = 0
		service.validate_supplier_pickup_order(doc)
		self.framework.get_doc.assert_not_called()
		self.framework.db.exists.assert_not_called()
