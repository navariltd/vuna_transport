# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from types import SimpleNamespace

from frappe.tests import UnitTestCase

from vuna_transport.services.execution.pod_service import (
	derive_delivery_order_execution_status,
	derive_pod_status,
)


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

	def test_partial_status(self):
		items = [SimpleNamespace(ordered_qty=10, delivered_qty=7, rejected_qty=0)]
		self.assertEqual(derive_pod_status(items), "Partially Delivered")

	def test_rejected_status(self):
		items = [SimpleNamespace(ordered_qty=10, delivered_qty=0, rejected_qty=10)]
		self.assertEqual(derive_pod_status(items), "Rejected")

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

	def test_open_exception_prevents_false_completion(self):
		loads = [SimpleNamespace(name="LOAD-1", status="Completed with Exceptions")]
		pods = [SimpleNamespace(delivery_load="LOAD-1", status="Partially Delivered")]
		exceptions = [SimpleNamespace(resolution_status="Open")]
		status = derive_delivery_order_execution_status(
			100, 100, 0, loads, pods, exceptions
		)
		self.assertEqual(status, "Partially Delivered")

	def test_resolved_exception_updates_completion(self):
		loads = [SimpleNamespace(name="LOAD-1", status="Completed with Exceptions")]
		pods = [SimpleNamespace(delivery_load="LOAD-1", status="Partially Delivered")]
		exceptions = [SimpleNamespace(resolution_status="Resolved")]
		status = derive_delivery_order_execution_status(
			100, 100, 0, loads, pods, exceptions
		)
		self.assertEqual(status, "Resolved with Exceptions")
