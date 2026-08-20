# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.utils import flt
from frappe.tests import UnitTestCase

from vuna_transport.services.delivery_order_service import (
	_get_group_key,
	_get_group_transport_requirements,
	_get_item_measure,
	_requires_transport_planning,
	_get_transport_quantity,
)
from vuna_transport.services.location_service import (
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

	def test_group_key_separates_delivery_address_and_warehouse(self):
		sales_order = SimpleNamespace(delivery_date="2026-08-12", customer_address="ADDR-1")
		item_a = SimpleNamespace(warehouse="WH-1", delivery_date=None)
		item_b = SimpleNamespace(warehouse="WH-2", delivery_date=None)

		self.assertNotEqual(_get_group_key(sales_order, item_a), _get_group_key(sales_order, item_b))

	def test_item_measure_uses_per_unit_value(self):
		item = SimpleNamespace(weight=None, weight_per_unit=2.5)
		self.assertEqual(flt(_get_item_measure(item, "weight", 4)), 10)

	@patch("vuna_transport.services.delivery_order_service.frappe.get_cached_doc")
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

	@patch("vuna_transport.services.delivery_order_service.frappe.get_cached_doc")
	def test_customer_pickup_can_skip_transport_planning(self, get_cached_doc):
		get_cached_doc.return_value = SimpleNamespace(
			name="Customer Pickup",
			disabled=0,
			requires_transport_planning=0,
		)
		sales_order = SimpleNamespace(vuna_delivery_method="Customer Pickup")
		self.assertFalse(_requires_transport_planning(sales_order))
