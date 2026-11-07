"""Shared Delivery Method purchasing semantics do not depend on display names."""

from itertools import product
from unittest import TestCase
from unittest.mock import patch

import frappe

from vuna_transport.services.demand import delivery_method_service as service
from vuna_transport.vuna_transport.doctype.delivery_method.delivery_method import DeliveryMethod


class TestPurchaseDeliveryMethods(TestCase):
	def setUp(self):
		self.method = frappe._dict(
			name="Our Supplier Collections",
			buying=1,
			selling=0,
			transport_arranged_by="Company",
			disabled=0,
			requires_transport_planning=1,
			requires_vehicle_allocation=1,
			requires_delivery_trip=1,
		)
		self.enterContext(patch.object(service.frappe, "get_cached_doc", return_value=self.method))
		self.enterContext(patch.object(service, "_", side_effect=lambda value: value))
		self.enterContext(patch.object(service.frappe, "throw", side_effect=ValueError))

	def test_custom_method_name_with_company_pickup_semantics_enables_collection(self):
		self.assertTrue(service.is_company_pickup_order(frappe._dict(vuna_delivery_method=self.method.name)))

	def test_blank_delivery_method_does_not_enable_company_collection(self):
		self.assertFalse(service.is_company_pickup_order(frappe._dict(vuna_delivery_method=None)))

	def test_supplier_delivery_and_disabled_methods_do_not_enable_pickup(self):
		self.method.transport_arranged_by = "Counterparty"
		self.method.requires_transport_planning = self.method.requires_vehicle_allocation = (
			self.method.requires_delivery_trip
		) = 0
		self.assertFalse(service.is_company_pickup_order(frappe._dict(vuna_delivery_method=self.method.name)))
		self.method.transport_arranged_by, self.method.disabled = "Company", 1
		self.method.requires_transport_planning = self.method.requires_vehicle_allocation = (
			self.method.requires_delivery_trip
		) = 1
		self.assertFalse(service.is_company_pickup_order(frappe._dict(vuna_delivery_method=self.method.name)))

	def test_sales_only_method_is_not_a_valid_purchasing_method(self):
		self.method.buying, self.method.selling = 0, 1
		self.assertFalse(service.get_purchase_delivery_method_workflow(self.method.name).allowed)

	def test_new_method_types_enforce_consistent_workflow_requirements(self):
		with patch.object(service.frappe, "throw", side_effect=ValueError):
			DeliveryMethod.validate(self.method)
			self.method.requires_delivery_trip = 0
			DeliveryMethod.validate(self.method)
			self.method.transport_arranged_by = "Counterparty"
			with self.assertRaises(ValueError):
				DeliveryMethod.validate(self.method)
			self.method.requires_transport_planning = self.method.requires_vehicle_allocation = 0
			DeliveryMethod.validate(self.method)

	def test_purchase_only_methods_cannot_generate_customer_delivery_orders(self):
		from vuna_transport.services.demand.delivery_order_service import _requires_transport_planning

		with (
			patch(
				"vuna_transport.services.demand.delivery_order_service.frappe.throw", side_effect=ValueError
			),
			self.assertRaises(ValueError),
		):
			_requires_transport_planning(frappe._dict(vuna_delivery_method=self.method.name))

	def test_both_scope_is_valid_for_sales_and_purchasing(self):
		self.method.buying = self.method.selling = 1
		service.validate_sales_order_delivery_method(frappe._dict(vuna_delivery_method=self.method.name))
		self.assertTrue(service.get_purchase_delivery_method_workflow(self.method.name).company_pickup)

	def test_company_booked_courier_does_not_bypass_planning(self):
		self.method.name = "Courier"
		self.assertTrue(service.is_company_pickup_order(frappe._dict(vuna_delivery_method="Courier")))
		self.method.transport_arranged_by = "Counterparty"
		self.method.requires_transport_planning = self.method.requires_vehicle_allocation = (
			self.method.requires_delivery_trip
		) = 0
		self.assertFalse(service.is_company_pickup_order(frappe._dict(vuna_delivery_method="Courier")))

	def test_workflow_dependency_matrix(self):
		for buying, selling, arranger, planning, allocation, trip in product(
			(0, 1), (0, 1), ("Company", "Counterparty"), (0, 1), (0, 1), (0, 1)
		):
			with self.subTest(
				buying=buying, selling=selling, arranger=arranger, workflow=(planning, allocation, trip)
			):
				self.method.update(
					buying=buying,
					selling=selling,
					transport_arranged_by=arranger,
					requires_transport_planning=planning,
					requires_vehicle_allocation=allocation,
					requires_delivery_trip=trip,
				)
				valid = bool(buying or selling) and (
					not any((planning, allocation, trip))
					if arranger == "Counterparty"
					else bool((not allocation or planning) and (not trip or allocation))
				)
				if valid:
					service.validate_method_configuration(self.method)
				else:
					with self.assertRaises(ValueError):
						service.validate_method_configuration(self.method)

	def test_missing_scope_and_responsibility_do_not_get_implicit_defaults(self):
		for values in ({"buying": 0, "selling": 0}, {"transport_arranged_by": None}):
			with patch.dict(self.method, values), self.assertRaises(ValueError):
				service.validate_method_configuration(self.method)

	def test_company_manual_workflow_still_permits_explicit_supplier_demand(self):
		self.method.requires_transport_planning = self.method.requires_vehicle_allocation = (
			self.method.requires_delivery_trip
		) = 0
		DeliveryMethod.validate(self.method)
		self.assertTrue(service.is_company_pickup_order(frappe._dict(vuna_delivery_method=self.method.name)))

	def test_company_manual_selling_allows_explicit_delivery_order_generation(self):
		from vuna_transport.services.demand.delivery_order_service import _requires_transport_planning

		self.method.buying, self.method.selling = 0, 1
		self.method.requires_transport_planning = self.method.requires_vehicle_allocation = (
			self.method.requires_delivery_trip
		) = 0
		self.assertTrue(_requires_transport_planning(frappe._dict(vuna_delivery_method=self.method.name)))

	def test_disabled_sales_method_is_rejected_server_side(self):
		self.method.buying, self.method.selling, self.method.disabled = 0, 1, 1
		with self.assertRaises(ValueError):
			service.validate_sales_order_delivery_method(frappe._dict(vuna_delivery_method=self.method.name))

	def test_purchase_order_rejects_sales_scope_even_when_named_company_pickup(self):
		from unittest.mock import MagicMock

		from vuna_transport.services.demand.supplier_pickup_demand_service import (
			validate_supplier_pickup_order,
		)

		self.method.name, self.method.buying, self.method.selling = "Company Pickup", 0, 1
		doc = MagicMock()
		doc.get_doc_before_save.return_value = None
		doc.get.side_effect = {"vuna_delivery_method": self.method.name}.get
		with self.assertRaises(ValueError):
			validate_supplier_pickup_order(doc)
