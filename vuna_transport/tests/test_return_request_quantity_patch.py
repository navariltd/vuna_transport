# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

from frappe import _dict
from frappe.tests import UnitTestCase

from vuna_transport.patches.migrate_return_request_quantities_to_sales_uom import (
	get_sales_uom_values,
)


class TestReturnRequestQuantityPatch(UnitTestCase):
	quantity_fields = (
		"delivered_qty",
		"requested_return_qty",
		"remaining_qty",
		"received_qty",
	)

	def test_sales_uom_pod_quantities_are_not_converted_twice(self):
		row = _dict(
			delivered_qty=2500,
			requested_return_qty=500,
			remaining_qty=500,
			received_qty=0,
		)
		delivery_item = _dict(uom="Carton", stock_uom="Pcs", conversion_factor=24)
		pod_item = _dict(uom="Carton", delivered_qty=2500, rejected_qty=500)

		values = get_sales_uom_values(row, delivery_item, pod_item, self.quantity_fields)

		self.assertEqual(values["requested_return_qty"], 500)
		self.assertEqual(values["delivered_qty"], 2500)
		self.assertEqual(values["remaining_qty"], 500)

	def test_legacy_stock_uom_quantities_are_converted(self):
		row = _dict(
			delivered_qty=3000,
			requested_return_qty=500,
			remaining_qty=500,
			received_qty=0,
		)
		delivery_item = _dict(uom="Carton", stock_uom="Pcs", conversion_factor=24)

		values = get_sales_uom_values(row, delivery_item, None, self.quantity_fields)

		self.assertEqual(values["requested_return_qty"], 500 / 24)
		self.assertEqual(values["delivered_qty"], 3000 / 24)
