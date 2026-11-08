"""One delivery-method policy for buying, selling, depot transfers and returns."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import frappe

from vuna_transport.services.demand import delivery_method_service as service


def source(doctype="Return Request", **values):
	data = dict(
		doctype=doctype,
		name="SOURCE",
		docstatus=0,
		delivery_method="Method",
		vuna_delivery_method="Method",
		material_request_type="Material Transfer",
		return_context="Customer",
	)
	data.update(values)
	doc = SimpleNamespace(**data)
	doc.get = lambda key: getattr(doc, key, None)
	doc.get_doc_before_save = lambda: None
	return doc


class TestTransferReturnDeliveryMethods(TestCase):
	def setUp(self):
		self.method = frappe._dict(
			name="Method",
			buying=0,
			selling=0,
			internal=1,
			disabled=0,
			transport_arranged_by="Company",
			requires_transport_planning=1,
			requires_vehicle_allocation=1,
			requires_delivery_trip=1,
		)
		self.enterContext(patch.object(service.frappe, "get_cached_doc", return_value=self.method))
		self.enterContext(patch.object(service, "_", side_effect=lambda message: message))
		self.enterContext(patch.object(service.frappe, "throw", side_effect=ValueError))
		self.references = self.enterContext(patch.object(service.frappe.db, "exists", return_value=False))

	def test_internal_only_method_is_valid(self):
		service.validate_method_configuration(self.method)

	def test_at_least_one_of_three_checkboxes_is_required(self):
		self.method.internal = 0
		with self.assertRaises(ValueError):
			service.validate_method_configuration(self.method)

	def test_transfer_accepts_internal_not_selling_only(self):
		doc = source("Material Request")
		self.assertTrue(service.get_source_delivery_method_workflow(doc).internal)
		self.method.internal, self.method.selling = 0, 1
		with self.assertRaises(ValueError):
			service.get_source_delivery_method_workflow(doc)

	def test_non_transfer_material_requests_are_unchanged(self):
		for request_type in ("Purchase", "Material Issue", "Manufacture"):
			self.assertIsNone(
				service.validate_source_delivery_method(
					source("Material Request", docstatus=1, material_request_type=request_type)
				)
			)

	def test_customer_returns_require_selling_not_buying(self):
		self.method.selling, self.method.internal = 1, 0
		self.assertTrue(service.get_source_delivery_method_workflow(source()).selling)
		self.method.selling, self.method.buying = 0, 1
		with self.assertRaises(ValueError):
			service.get_source_delivery_method_workflow(source())

	def test_depot_returns_require_internal_including_derived_depot_context(self):
		for values in ({"return_context": "Depot"}, {"source_stock_entry": "STE"}):
			self.assertTrue(service.get_source_delivery_method_workflow(source(**values)).internal)
		self.method.internal, self.method.selling = 0, 1
		with self.assertRaises(ValueError):
			service.get_source_delivery_method_workflow(source(return_context="Depot"))

	def test_one_method_can_support_both_customer_and_depot_returns(self):
		self.method.selling = 1
		service.validate_source_delivery_method(source())
		service.validate_source_delivery_method(source(return_context="Depot"))

	def test_disabled_method_rejected(self):
		self.method.disabled = 1
		with self.assertRaises(ValueError):
			service.get_source_delivery_method_workflow(source(return_context="Depot"))

	def test_submitted_transfer_and_return_require_a_method(self):
		for doctype in ("Material Request", "Return Request"):
			with self.assertRaises(ValueError):
				service.validate_source_delivery_method(
					source(doctype, docstatus=1, delivery_method=None, vuna_delivery_method=None)
				)

	def test_counterparty_dropoff_does_not_create_company_pickup(self):
		self.method.selling = 1
		self.method.transport_arranged_by = "Counterparty"
		self.method.requires_transport_planning = self.method.requires_vehicle_allocation = (
			self.method.requires_delivery_trip
		) = 0
		self.assertFalse(service.source_uses_company_transport(source()))

	def test_method_change_is_blocked_once_demand_exists(self):
		doc = source("Material Request")
		doc.get_doc_before_save = lambda: source("Material Request", vuna_delivery_method="Previous")
		self.references.return_value = True
		with self.assertRaises(ValueError):
			service.validate_source_delivery_method(doc)

	def test_return_method_change_is_blocked_by_pickup_assignment(self):
		self.method.selling = 1
		doc = source()
		doc.get_doc_before_save = lambda: source(delivery_method="Previous")
		self.references.side_effect = [False, True]
		with self.assertRaises(ValueError):
			service.validate_source_delivery_method(doc)

	def test_method_changes_do_not_rewrite_custody(self):
		self.method.selling = 1
		doc = source(custody="Still With Vehicle", custody_events=["original evidence"])
		service.validate_source_delivery_method(doc)
		self.assertEqual(doc.custody, "Still With Vehicle")
		self.assertEqual(doc.custody_events, ["original evidence"])
		self.assertEqual(doc.transport_arranged_by, "Company")
