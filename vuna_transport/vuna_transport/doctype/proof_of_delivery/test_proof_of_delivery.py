# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

from frappe.exceptions import ValidationError
from frappe.tests import UnitTestCase

from vuna_transport.services.execution.pod_service import (
	derive_delivery_order_execution_status,
	derive_pod_status,
	prepare_pod_cancellation,
	validate_pod_items,
)
from vuna_transport.services.execution.return_service import get_pod_quantity_in_stock_uom

# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]


class TestProofOfDelivery(UnitTestCase):
	"""
	Integration tests for ProofOfDelivery.
	Use this class for testing interactions between multiple components.
	"""

	def test_accepted_status(self):
		items = [SimpleNamespace(ordered_qty=10, delivered_qty=10, rejected_qty=0)]
		self.assertEqual(derive_pod_status(items), "Accepted")

	@patch("vuna_transport.vuna_transport.doctype.proof_of_delivery.proof_of_delivery.frappe.db.get_value")
	def test_duplicate_active_pod_is_rejected(self, get_value):
		from vuna_transport.vuna_transport.doctype.proof_of_delivery.proof_of_delivery import (
			ProofofDelivery,
		)

		get_value.return_value = "POD-EXISTING"
		pod = SimpleNamespace(
			name="POD-COPY",
			delivery_trip="DT-1",
			delivery_stop="STOP-1",
		)

		with self.assertRaises(ValidationError):
			ProofofDelivery.validate_unique_active_pod(pod)

	@patch("vuna_transport.services.execution.pod_service.frappe.db.set_value")
	@patch(
		"vuna_transport.services.execution.pod_service.frappe.db.get_value",
		return_value="POD-DRAFT",
	)
	@patch("vuna_transport.services.execution.pod_service.frappe.db.exists", return_value=True)
	def test_deleted_draft_pod_clears_delivery_stop_reference(self, _exists, _get_value, set_value):
		from vuna_transport.services.execution.pod_service import clear_pod_stop_reference

		clear_pod_stop_reference(SimpleNamespace(delivery_stop="STOP-1", name="POD-DRAFT"))

		set_value.assert_called_once_with(
			"Delivery Stop", "STOP-1", "vuna_proof_of_delivery", None, update_modified=False
		)

	def test_partial_status(self):
		items = [SimpleNamespace(ordered_qty=10, delivered_qty=7, rejected_qty=0)]
		self.assertEqual(derive_pod_status(items), "Partially Delivered")

	def test_rejected_status(self):
		items = [SimpleNamespace(ordered_qty=10, delivered_qty=0, rejected_qty=10)]
		self.assertEqual(derive_pod_status(items), "Rejected")

	def test_rejected_quantity_follows_delivered_quantity(self):
		item = SimpleNamespace(
			item_code="Milk",
			ordered_qty=10,
			delivered_qty=7,
			rejected_qty=0,
			rejection_reasons=[SimpleNamespace(rejection_reason="Product Damaged")],
		)

		validate_pod_items([item])

		self.assertEqual(item.rejected_qty, 3)

	def test_rejected_quantity_requires_at_least_one_reason(self):
		item = SimpleNamespace(
			item_code="Milk",
			ordered_qty=10,
			delivered_qty=7,
			rejected_qty=0,
			rejection_reasons=[],
		)

		with self.assertRaises(ValidationError):
			validate_pod_items([item])

	def test_pod_sales_uom_quantity_converts_to_stock_uom_for_return(self):
		pod_item = SimpleNamespace(uom="Carton", rejected_qty=2)
		delivery_note_item = SimpleNamespace(conversion_factor=12)

		self.assertEqual(
			get_pod_quantity_in_stock_uom(pod_item, delivery_note_item, "rejected_qty"),
			24,
		)

	def test_legacy_pod_quantity_remains_in_stock_uom(self):
		pod_item = SimpleNamespace(uom=None, rejected_qty=24)
		delivery_note_item = SimpleNamespace(conversion_factor=12)

		self.assertEqual(
			get_pod_quantity_in_stock_uom(pod_item, delivery_note_item, "rejected_qty"),
			24,
		)

	def test_item_rejection_reason_satisfies_validation(self):
		item = SimpleNamespace(
			item_code="Milk",
			ordered_qty=10,
			delivered_qty=7,
			rejected_qty=0,
			rejection_reason="Product Damaged",
		)

		validate_pod_items([item])
		self.assertEqual(item.rejected_qty, 3)

	def test_first_of_two_partial_loads_does_not_complete_delivery_order(self):
		loads = [
			SimpleNamespace(name="LOAD-1", status="Completed"),
			SimpleNamespace(name="LOAD-2", status="Dispatched"),
		]
		pods = [SimpleNamespace(delivery_load="LOAD-1", status="Accepted")]
		status = derive_delivery_order_execution_status(100, 100, 0, loads, pods)
		self.assertEqual(status, "Partially Delivered")

	def test_two_accepted_partial_loads_complete_delivery_order(self):
		loads = [
			SimpleNamespace(name="LOAD-1", status="Completed"),
			SimpleNamespace(name="LOAD-2", status="Completed"),
		]
		pods = [
			SimpleNamespace(delivery_load="LOAD-1", status="Accepted"),
			SimpleNamespace(delivery_load="LOAD-2", status="Accepted"),
		]
		status = derive_delivery_order_execution_status(100, 100, 0, loads, pods)
		self.assertEqual(status, "Delivered")

	def test_partial_delivery_remains_partial_after_follow_up_return(self):
		loads = [SimpleNamespace(name="LOAD-1", status="Completed with Exceptions")]
		pods = [SimpleNamespace(delivery_load="LOAD-1", status="Partially Delivered")]
		status = derive_delivery_order_execution_status(100, 100, 0, loads, pods)
		self.assertEqual(status, "Partially Delivered")

	def test_failed_delivery_remains_a_delivery_outcome(self):
		loads = [SimpleNamespace(name="LOAD-1", status="Completed with Exceptions")]
		pods = [SimpleNamespace(delivery_load="LOAD-1", status="Failed")]
		status = derive_delivery_order_execution_status(100, 100, 0, loads, pods)
		self.assertEqual(status, "Partially Delivered")

	@patch("vuna_transport.services.execution.pod_service.frappe.db.table_exists", return_value=False)
	def test_pod_cancellation_ignores_parent_trip_link_after_own_checks(self, _table_exists):
		pod = SimpleNamespace(name="POD-1")

		prepare_pod_cancellation(pod)

		self.assertEqual(pod.ignore_linked_doctypes, ["Delivery Trip"])

	@patch(
		"vuna_transport.services.execution.pod_service.frappe.db.get_value",
		return_value="RR-1",
	)
	@patch("vuna_transport.services.execution.pod_service.frappe.db.table_exists", return_value=True)
	def test_pod_cancellation_rejects_active_return_request(self, _table_exists, _get_value):
		with self.assertRaises(ValidationError):
			prepare_pod_cancellation(SimpleNamespace(name="POD-1"))
