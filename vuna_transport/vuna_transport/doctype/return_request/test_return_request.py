# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

from frappe.exceptions import ValidationError
from frappe.tests import UnitTestCase

from vuna_transport.services.execution.return_service import (
	_validate_status_requirements,
	get_remaining_returnable_quantity,
)


class TestReturnRequest(UnitTestCase):
	def test_received_quantity_must_be_inspected_before_resolution(self):
		request = SimpleNamespace(
			status="Awaiting Resolution",
			items=[
				SimpleNamespace(
					item_code="MILK", received_qty=10, accepted_return_qty=9, rejected_return_qty=0
				)
			],
		)
		with self.assertRaises(ValidationError):
			_validate_status_requirements(request)

	def test_resolution_requires_accepted_quantity_to_be_accounted_for(self):
		request = SimpleNamespace(
			status="Resolved",
			items=[
				SimpleNamespace(
					item_code="MILK",
					received_qty=10,
					accepted_return_qty=10,
					rejected_return_qty=0,
					resolution_type=None,
					resolution_qty=0,
				)
			],
		)
		with self.assertRaises(ValidationError):
			_validate_status_requirements(request)

	@patch("vuna_transport.services.execution.return_service.frappe.db.sql")
	@patch("vuna_transport.services.execution.return_service.frappe.db.get_value")
	def test_previous_returns_reduce_remaining_returnable_quantity(self, get_value, sql):
		get_value.return_value = {"parent": "DN-1", "stock_qty": 100, "qty": 100}
		sql.side_effect = [[(20,)], [(30,)]]
		self.assertEqual(get_remaining_returnable_quantity("DNI-1", "RR-2"), 50)

	def test_status_lifecycle_is_controlled(self):
		from vuna_transport.services.execution.return_service import STATUS_TRANSITIONS

		self.assertEqual(STATUS_TRANSITIONS["request"], ("Draft", "Requested"))
		self.assertEqual(STATUS_TRANSITIONS["close"], (("Resolved",), "Closed"))
