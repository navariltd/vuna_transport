# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


class TransportCostStructureAssignment(Document):
	def before_validate(self):
		self._populate_components_from_structure()
		self._sync_component_natures()

	def validate(self):
		if self.valid_from and self.valid_to and getdate(self.valid_to) < getdate(self.valid_from):
			frappe.throw(_("Valid To cannot be before Valid From."))
		if self.vehicle and self.supplier:
			frappe.throw(
				_("A Cost Structure Assignment cannot be restricted to both a Vehicle and a Supplier.")
			)
		model_company = frappe.db.get_value("Transport Cost Structure", self.cost_structure, "company")
		structure_docstatus = frappe.db.get_value(
			"Transport Cost Structure", self.cost_structure, "docstatus"
		)
		if structure_docstatus != 1:
			frappe.throw(
				_("Cost Structure {0} must be submitted before it can be assigned.").format(
					self.cost_structure
				)
			)
		if model_company and model_company != self.company:
			frappe.throw(
				_("Cost Structure {0} belongs to Company {1}.").format(self.cost_structure, model_company)
			)
		if frappe.db.get_value("Transport Cost Structure", self.cost_structure, "disabled"):
			frappe.throw(_("Disabled Cost Structure {0} cannot be assigned.").format(self.cost_structure))
		seen = set()
		structure_components = {
			row.cost_component: row
			for row in frappe.get_all(
				"Transport Cost Structure Component",
				filters={"parent": self.cost_structure, "parenttype": "Transport Cost Structure"},
				fields=["cost_component", "required"],
			)
		}
		unknown_components = set()
		for row in self.components:
			if row.cost_component not in structure_components:
				unknown_components.add(row.cost_component)
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
				frappe.throw(_("Rate and UOM are required for {0}.").format(dimension))
			if dimension in {"Flat Per Trip", "Monthly Fixed"} and row.flat_amount is None:
				frappe.throw(_("Flat Amount is required for {0}.").format(dimension))
		if unknown_components:
			frappe.throw(
				_("These components are not defined in Cost Structure {0}: {1}.").format(
					self.cost_structure, ", ".join(sorted(unknown_components))
				)
			)
		missing_required = {
			name for name, row in structure_components.items() if row.required and name not in seen
		}
		if missing_required:
			frappe.throw(
				_("Required components are missing from this assignment: {0}.").format(
					", ".join(sorted(missing_required))
				)
			)
		destinations = set()
		for row in self.destination_rates:
			if row.source_warehouse:
				warehouse_company = frappe.db.get_value("Warehouse", row.source_warehouse, "company")
				if warehouse_company and warehouse_company != self.company:
					frappe.throw(
						_("From Warehouse {0} belongs to Company {1}.").format(
							row.source_warehouse, warehouse_company
						)
					)
			if not row.territory and not row.destination_key:
				frappe.throw(_("Territory is required for every destination rate."))
			if not row.territory and row.destination_key:
				continue
			if row.county:
				county_territory = frappe.db.get_value("County", row.county, "territory")
				if county_territory and county_territory != row.territory:
					frappe.throw(
						_("County {0} does not belong to Territory {1}.").format(row.county, row.territory)
					)
			if row.town and not row.county:
				frappe.throw(_("County is required when a Town is selected."))
			if row.town and row.county:
				town_county = frappe.db.get_value("Town", row.town, "county")
				if town_county and town_county != row.county:
					frappe.throw(_("Town {0} does not belong to County {1}.").format(row.town, row.county))
			key = (row.source_warehouse or "", row.territory, row.county or "", row.town or "")
			if key in destinations:
				frappe.throw(
					_("Destination geography {0} is duplicated.").format(
						" / ".join(value for value in key if value)
					)
				)
			destinations.add(key)
		if self.vehicle:
			vehicle = frappe.db.get_value(
				"Vehicle", self.vehicle, ["company", "vuna_transport_cost_structure"], as_dict=True
			)
			if vehicle and vehicle.company and vehicle.company != self.company:
				frappe.throw(_("Vehicle {0} belongs to Company {1}.").format(self.vehicle, vehicle.company))
			if vehicle and vehicle.vuna_transport_cost_structure != self.cost_structure:
				frappe.throw(
					_("Vehicle {0} uses Cost Structure {1}; this assignment uses {2}.").format(
						self.vehicle,
						vehicle.vuna_transport_cost_structure or _("None"),
						self.cost_structure,
					)
				)

	def _populate_components_from_structure(self):
		if not self.cost_structure or self.components:
			return
		structure = frappe.get_doc("Transport Cost Structure", self.cost_structure)
		for component in structure.components:
			self.append(
				"components",
				{
					"cost_component": component.cost_component,
					"calculation_basis": component.calculation_basis,
					"cost_nature": component.cost_nature,
				},
			)

	def _sync_component_natures(self):
		if not self.cost_structure:
			return
		structure_components = {
			row.cost_component: row.cost_nature
			for row in frappe.get_all(
				"Transport Cost Structure Component",
				filters={"parent": self.cost_structure, "parenttype": "Transport Cost Structure"},
				fields=["cost_component", "cost_nature"],
			)
		}
		for row in self.components:
			if row.cost_component in structure_components:
				row.cost_nature = structure_components[row.cost_component]
