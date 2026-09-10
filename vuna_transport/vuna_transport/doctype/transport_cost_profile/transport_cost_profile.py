# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


class TransportCostProfile(Document):
	def validate(self):
		if self.valid_from and self.valid_to and getdate(self.valid_to) < getdate(self.valid_from):
			frappe.throw(_("Valid To cannot be before Valid From."))
		if self.vehicle and self.supplier:
			frappe.throw(_("A Cost Profile cannot be restricted to both a Vehicle and a Supplier."))
		model_company = frappe.db.get_value("Transport Costing Model", self.costing_model, "company")
		if model_company and model_company != self.company:
			frappe.throw(
				_("Costing Model {0} belongs to Company {1}.").format(self.costing_model, model_company)
			)
		if frappe.db.get_value("Transport Costing Model", self.costing_model, "disabled"):
			frappe.throw(_("Disabled Costing Model {0} cannot be assigned.").format(self.costing_model))
		seen = set()
		for row in self.components:
			if row.cost_component in seen:
				frappe.throw(_("Cost Component {0} cannot be repeated.").format(row.cost_component))
			seen.add(row.cost_component)
			component = frappe.db.get_value(
				"Transport Cost Component",
				row.cost_component,
				["disabled", "default_cost_nature"],
				as_dict=True,
			)
			if component and component.disabled:
				frappe.throw(_("Disabled Cost Component {0} cannot be assigned.").format(row.cost_component))
			if row.calculation_basis == "Destination Rate" and not self.destination_rates:
				frappe.throw(_("Add destination rates for a Destination Rate component."))
			dimension = row.calculation_basis
			if dimension in {"Per Distance", "Per Fuel Quantity"} and (not row.rate or not row.rate_uom):
				frappe.throw(_("Rate and Rate UOM are required for {0}.").format(dimension))
			if dimension in {"Flat Per Trip", "Monthly Fixed"} and row.flat_amount is None:
				frappe.throw(_("Flat Amount is required for {0}.").format(dimension))
		destinations = set()
		for row in self.destination_rates:
			if row.destination_key in destinations:
				frappe.throw(_("Destination {0} is duplicated.").format(row.destination_key))
			destinations.add(row.destination_key)
		if self.vehicle:
			vehicle = frappe.db.get_value(
				"Vehicle", self.vehicle, ["company", "vuna_transport_costing_model"], as_dict=True
			)
			if vehicle and vehicle.company and vehicle.company != self.company:
				frappe.throw(_("Vehicle {0} belongs to Company {1}.").format(self.vehicle, vehicle.company))
			if vehicle and vehicle.vuna_transport_costing_model != self.costing_model:
				frappe.throw(
					_("Vehicle {0} uses Costing Model {1}; this profile uses {2}.").format(
						self.vehicle, vehicle.vuna_transport_costing_model or _("None"), self.costing_model
					)
				)
		self._validate_overlapping_profiles()

	def _validate_overlapping_profiles(self):
		profiles = frappe.get_all(
			"Transport Cost Profile",
			filters={"company": self.company, "disabled": 0, "name": ["!=", self.name]},
			fields=[
				"name",
				"costing_model",
				"vehicle",
				"supplier",
				"vehicle_category",
				"valid_from",
				"valid_to",
			],
		)
		for profile in profiles:
			if any(
				getattr(self, field, None) != getattr(profile, field, None)
				for field in ("costing_model", "vehicle", "supplier", "vehicle_category")
			):
				continue
			if _date_ranges_overlap(self.valid_from, self.valid_to, profile.valid_from, profile.valid_to):
				frappe.throw(
					_("Cost Profile {0} overlaps this profile's validity period for the same scope.").format(
						profile.name
					)
				)


def _date_ranges_overlap(first_from, first_to, second_from, second_to):
	return not (
		(first_to and second_from and getdate(first_to) < getdate(second_from))
		or (second_to and first_from and getdate(second_to) < getdate(first_from))
	)
