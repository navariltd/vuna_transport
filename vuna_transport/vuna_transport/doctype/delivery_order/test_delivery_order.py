# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.exceptions import ValidationError
from frappe.tests import UnitTestCase
from frappe.utils import flt

from vuna_transport.services.demand.delivery_order_service import (
	_get_delivery_order_item_values,
	_get_group_key,
	_get_group_transport_requirements,
	_get_item_measure,
	_get_transport_quantity,
	_is_stock_item,
	_requires_transport_planning,
	get_delivery_order_generation_status,
	get_sales_order_item_quantities,
	sync_delivery_order_quantities,
	sync_delivery_order_status_from_sales_order,
	sync_delivery_orders_after_direct_delivery_note,
)
from vuna_transport.services.routing.location_service import (
	get_point_coordinates,
	make_point_geojson,
	synchronize_address_location,
	validate_point_coordinates,
)

# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]


class TestDeliveryOrder(UnitTestCase):
	def test_post_submit_sync_fields_are_allowed_to_update(self):
		for fieldname in (
			"ordered_qty",
			"transport_qty",
			"uom",
			"conversion_factor",
			"stock_qty",
			"stock_uom",
		):
			self.assertEqual(
				frappe.get_meta("Delivery Order Item").get_field(fieldname).allow_on_submit,
				1,
				fieldname,
			)

	"""
	Integration tests for DeliveryOrder.
	Use this class for testing interactions between multiple components.
	"""

	def test_transport_quantity_excludes_delivered_quantity(self):
		item = SimpleNamespace(qty=12, delivered_qty=4)
		self.assertEqual(_get_transport_quantity(item), 8)

	def test_transport_quantity_never_becomes_negative(self):
		item = SimpleNamespace(qty=4, delivered_qty=8)
		self.assertEqual(_get_transport_quantity(item), 0)

	@patch("vuna_transport.services.demand.delivery_order_service.frappe.get_cached_doc")
	def test_service_item_is_not_transportable(self, get_cached_doc):
		get_cached_doc.return_value = SimpleNamespace(is_stock_item=0)

		self.assertFalse(_is_stock_item(SimpleNamespace(item_code="INSTALLATION-SERVICE")))

	@patch("vuna_transport.services.demand.delivery_order_service._create_delivery_order")
	@patch(
		"vuna_transport.services.demand.delivery_order_service._is_item_already_linked", return_value=False
	)
	@patch("vuna_transport.services.demand.delivery_order_service._get_group_key")
	@patch("vuna_transport.services.demand.delivery_order_service._get_transport_quantity", return_value=1)
	@patch("vuna_transport.services.demand.delivery_order_service._is_stock_item")
	@patch(
		"vuna_transport.services.demand.delivery_order_service._requires_transport_planning",
		return_value=True,
	)
	@patch("vuna_transport.services.demand.delivery_order_service._validate_sales_order")
	@patch("vuna_transport.services.demand.delivery_order_service._get_sales_order")
	def test_service_items_are_excluded_when_sales_order_contains_stock_items(
		self,
		get_sales_order,
		_validate_sales_order,
		_requires_transport_planning,
		is_stock_item,
		_get_transport_quantity,
		_get_group_key,
		_is_item_already_linked,
		_create_delivery_order,
	):
		stock_item = SimpleNamespace(name="SOI-STOCK", item_code="MILK")
		service_item = SimpleNamespace(name="SOI-SERVICE", item_code="DELIVERY-SERVICE")
		sales_order = SimpleNamespace(doctype="Sales Order", name="SO-TEST", items=[stock_item, service_item])
		get_sales_order.return_value = sales_order
		is_stock_item.side_effect = lambda item: item is stock_item
		_get_group_key.return_value = (
			"2026-08-12",
			"ADDR-1",
			"WH-1",
			None,
			None,
			None,
			"Company Delivery",
			None,
		)
		_create_delivery_order.return_value = "DO-TEST"

		from vuna_transport.services.demand.delivery_order_service import (
			create_delivery_orders_for_sales_order,
		)

		self.assertEqual(create_delivery_orders_for_sales_order(sales_order), ["DO-TEST"])
		_create_delivery_order.assert_called_once()
		self.assertEqual(_create_delivery_order.call_args.args[2], [(stock_item, 1)])

	@patch(
		"vuna_transport.services.demand.delivery_order_service._is_item_already_linked", return_value=False
	)
	@patch("vuna_transport.services.demand.delivery_order_service._get_transport_quantity", return_value=1)
	@patch("vuna_transport.services.demand.delivery_order_service._is_stock_item", return_value=True)
	@patch(
		"vuna_transport.services.demand.delivery_order_service._requires_transport_planning",
		return_value=True,
	)
	@patch("vuna_transport.services.demand.delivery_order_service.require_document_permission")
	@patch("vuna_transport.services.demand.delivery_order_service._get_sales_order")
	def test_generation_status_is_available_for_missing_delivery_orders(
		self,
		get_sales_order,
		_require_document_permission,
		_requires_transport_planning,
		_is_stock_item,
		_get_transport_quantity,
		_is_item_already_linked,
	):
		item = SimpleNamespace(name="SOI-1", item_code="MILK")
		get_sales_order.return_value = SimpleNamespace(docstatus=1, items=[item])

		self.assertEqual(get_delivery_order_generation_status("SO-TEST"), {"can_generate": True})

	def test_sales_order_quantities_are_authoritative(self):
		item = SimpleNamespace(qty=100, delivered_qty=30)
		self.assertEqual(get_sales_order_item_quantities(item), (100, 70))

	@patch("vuna_transport.services.demand.delivery_order_service.frappe.get_all")
	def test_sales_return_does_not_resynchronize_outbound_delivery_orders(self, get_all):
		sync_delivery_orders_after_direct_delivery_note(
			SimpleNamespace(
				is_return=1,
				vuna_delivery_load=None,
				items=[SimpleNamespace(against_sales_order="SO-TEST")],
			)
		)

		get_all.assert_not_called()

	@patch("vuna_transport.services.demand.delivery_order_service.frappe.db.set_value")
	def test_closed_sales_order_closes_open_delivery_order(self, set_value):
		delivery_order = SimpleNamespace(name="DO-TEST", sales_order="SO-TEST", docstatus=1, status="Open")
		sales_order = SimpleNamespace(docstatus=1, status="Closed")

		sync_delivery_order_status_from_sales_order(delivery_order, sales_order)

		self.assertEqual(delivery_order.status, "Closed")
		set_value.assert_called_once_with(
			"Delivery Order", "DO-TEST", "status", "Closed", update_modified=False
		)

	@patch("vuna_transport.services.demand.delivery_order_service._get_item_transport_measurements")
	def test_delivery_order_item_preserves_sales_uom_and_stock_quantity(self, get_measurements):
		get_measurements.return_value = SimpleNamespace(
			weight=120, weight_uom="Kg", volume=12, volume_uom="Cubic Meter"
		)
		item = SimpleNamespace(
			name="SOI-1",
			item_code="MILK-CARTON",
			item_name="Milk Carton",
			qty=10,
			uom="Carton",
			stock_uom="Litre",
			conversion_factor=12,
			warehouse="Main Warehouse",
		)

		values = _get_delivery_order_item_values(item, 10)

		self.assertEqual(values["uom"], "Carton")
		self.assertEqual(values["stock_uom"], "Litre")
		self.assertEqual(values["conversion_factor"], 12)
		self.assertEqual(values["stock_qty"], 120)

	@patch("vuna_transport.services.demand.source_policy_service.get_source_policy")
	def test_auto_submit_delivery_orders_setting_is_disabled_by_default(self, get_single_value):
		get_single_value.return_value = SimpleNamespace(auto_submit_delivery_orders=False)
		from vuna_transport.services.demand.delivery_order_service import (
			_should_auto_submit_delivery_orders,
		)

		self.assertFalse(_should_auto_submit_delivery_orders())

	@patch("vuna_transport.services.demand.source_policy_service.get_source_policy")
	def test_auto_submit_delivery_orders_setting_can_be_enabled(self, get_single_value):
		get_single_value.return_value = SimpleNamespace(auto_submit_delivery_orders=True)
		from vuna_transport.services.demand.delivery_order_service import (
			_should_auto_submit_delivery_orders,
		)

		self.assertTrue(_should_auto_submit_delivery_orders())

	@patch(
		"vuna_transport.services.demand.delivery_order_service._get_delivery_order_item_commitments",
		return_value=(0, 0),
	)
	@patch("vuna_transport.services.demand.delivery_order_service._get_item_transport_measurements")
	@patch("vuna_transport.services.demand.delivery_order_service._get_sales_order")
	def test_submitted_sales_order_quantity_refreshes_delivery_order_item(
		self, get_sales_order, get_measurements, _get_commitments
	):
		sales_item = SimpleNamespace(name="SOI-1", qty=100, delivered_qty=25)
		row = SimpleNamespace(
			name="DOI-1",
			sales_order_item="SOI-1",
			item_code="MILK",
			ordered_qty=50,
			transport_qty=50,
			weight=50,
			weight_uom="Kg",
			volume=5,
			volume_uom="m3",
			pallets=1,
			cases=1,
			crates=1,
		)
		get_sales_order.return_value = SimpleNamespace(items=[sales_item])
		get_measurements.return_value = SimpleNamespace(
			weight=75, weight_uom="Kg", volume=7.5, volume_uom="m3"
		)
		delivery_order = SimpleNamespace(sales_order="SO-1", items=[row])

		self.assertTrue(sync_delivery_order_quantities(delivery_order))
		self.assertEqual(row.ordered_qty, 100)
		self.assertEqual(row.transport_qty, 75)
		self.assertEqual(row.weight, 75)
		self.assertEqual(row.volume, 7.5)

	@patch(
		"vuna_transport.services.demand.delivery_order_service._get_delivery_order_item_commitments",
		return_value=(10, 40),
	)
	@patch("vuna_transport.services.demand.delivery_order_service._get_sales_order")
	def test_sales_order_quantity_cannot_drop_below_active_load_commitments(
		self, get_sales_order, _get_commitments
	):
		get_sales_order.return_value = SimpleNamespace(
			items=[SimpleNamespace(name="SOI-1", qty=45, delivered_qty=0)]
		)
		delivery_order = SimpleNamespace(
			sales_order="SO-1",
			items=[SimpleNamespace(name="DOI-1", sales_order_item="SOI-1", item_code="MILK")],
		)

		with self.assertRaises(ValidationError):
			sync_delivery_order_quantities(delivery_order)

	def test_delivery_order_quantities_are_read_only(self):
		meta = frappe.get_meta("Delivery Order Item")
		self.assertTrue(meta.get_field("ordered_qty").read_only)
		self.assertTrue(meta.get_field("transport_qty").read_only)

	def test_group_key_separates_delivery_address_and_warehouse(self):
		sales_order = SimpleNamespace(delivery_date="2026-08-12", customer_address="ADDR-1")
		item_a = SimpleNamespace(warehouse="WH-1", delivery_date=None)
		item_b = SimpleNamespace(warehouse="WH-2", delivery_date=None)

		self.assertNotEqual(_get_group_key(sales_order, item_a), _get_group_key(sales_order, item_b))

	@patch("vuna_transport.services.demand.delivery_order_service.frappe.get_cached_doc")
	def test_delivery_group_is_used_in_group_key(self, get_cached_doc):
		get_cached_doc.side_effect = [
			SimpleNamespace(vuna_delivery_group="Packed"),
			SimpleNamespace(name="Packed", disabled=0, company=None),
			SimpleNamespace(vuna_delivery_group="Bulk"),
			SimpleNamespace(name="Bulk", disabled=0, company=None),
		]
		sales_order = SimpleNamespace(
			delivery_date="2026-08-12", customer_address="ADDR-1", company="Company A"
		)
		item_a = SimpleNamespace(warehouse="WH-1", delivery_date=None, item_code="ITEM-1")
		item_b = SimpleNamespace(warehouse="WH-1", delivery_date=None, item_code="ITEM-2")

		key_a = _get_group_key(sales_order, item_a)
		key_b = _get_group_key(sales_order, item_b)
		self.assertNotEqual(key_a, key_b)
		self.assertEqual(key_a[-1], "Packed")

	def test_item_measure_uses_per_unit_value(self):
		item = SimpleNamespace(weight=None, weight_per_unit=2.5)
		self.assertEqual(flt(_get_item_measure(item, "weight", 4)), 10)

	@patch("vuna_transport.services.demand.delivery_order_service.frappe.get_cached_doc")
	def test_explicit_item_transport_requirements_are_aggregated(self, get_cached_doc):
		get_cached_doc.side_effect = [
			SimpleNamespace(
				vuna_refrigeration_required=1,
				vuna_tail_lift_required=0,
				vuna_required_body_type="Rigid Truck",
				vuna_special_equipment_required="Temperature Logger",
			),
			SimpleNamespace(
				vuna_refrigeration_required=0,
				vuna_tail_lift_required=1,
				vuna_required_body_type="Rigid Truck",
				vuna_special_equipment_required="Straps",
			),
		]
		rows = [
			(SimpleNamespace(item_code="ITEM-1"), 2),
			(SimpleNamespace(item_code="ITEM-2"), 3),
		]
		result = _get_group_transport_requirements(rows)
		self.assertTrue(result.refrigeration_required)
		self.assertTrue(result.tail_lift_required)
		self.assertEqual(result.required_body_type, "Rigid Truck")
		self.assertEqual(result.special_equipment_required, "Straps, Temperature Logger")

	def test_point_coordinates_use_geojson_longitude_latitude_order(self):
		location = (
			'{"type":"FeatureCollection","features":[{"type":"Feature",'
			'"properties":{},"geometry":{"type":"Point","coordinates":[36.8219,-1.2921]}}]}'
		)
		latitude, longitude = get_point_coordinates(location)
		self.assertEqual(latitude, -1.2921)
		self.assertEqual(longitude, 36.8219)

	def test_coordinate_entry_builds_address_map_point(self):
		address = frappe.new_doc("Address")
		address.vuna_latitude = -1.2921
		address.vuna_longitude = 36.8219
		synchronize_address_location(address)
		self.assertEqual(get_point_coordinates(address.vuna_location), (-1.2921, 36.8219))

	def test_map_point_populates_address_coordinates(self):
		address = frappe.new_doc("Address")
		address.vuna_location = make_point_geojson(-1.2921, 36.8219)
		synchronize_address_location(address)
		self.assertEqual(address.vuna_latitude, -1.2921)
		self.assertEqual(address.vuna_longitude, 36.8219)

	def test_coordinate_ranges_are_validated(self):
		with self.assertRaises(frappe.ValidationError):
			validate_point_coordinates(91, 36.8219)
		with self.assertRaises(frappe.ValidationError):
			validate_point_coordinates(-1.2921, 181)

	@patch("vuna_transport.services.demand.delivery_order_service.frappe.get_cached_doc")
	def test_customer_pickup_can_skip_transport_planning(self, get_cached_doc):
		get_cached_doc.return_value = SimpleNamespace(
			name="Customer Pickup",
			buying=0,
			selling=1,
			transport_arranged_by="Counterparty",
			requires_vehicle_allocation=0,
			requires_delivery_trip=0,
			disabled=0,
			requires_transport_planning=0,
		)
		sales_order = SimpleNamespace(vuna_delivery_method="Customer Pickup")
		self.assertFalse(_requires_transport_planning(sales_order))
