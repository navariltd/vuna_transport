# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class TransportCostStructure(Document):
	def before_validate(self):
		for row in self.components:
			if row.cost_component:
				row.cost_nature = frappe.db.get_value(
					"Transport Cost Component", row.cost_component, "default_cost_nature"
				)

	def validate(self):
		if self.vehicle_owner and self.vehicle_owner not in {"Company", "Supplier"}:
			frappe.throw(_("Vehicle Owner must be Company or Supplier."))
		seen = set()
		for row in self.components:
			if row.cost_component in seen:
				frappe.throw(_("Cost Component {0} cannot be repeated.").format(row.cost_component))
			seen.add(row.cost_component)
			if row.calculation_basis not in {
				"Flat Per Trip",
				"Per Distance",
				"Per Fuel Quantity",
				"Monthly Fixed",
				"Destination Rate",
				"Manual / Actual",
			}:
				frappe.throw(_("Select a valid calculation basis for row {0}.").format(row.idx))
			if row.cost_nature not in {"Fixed", "Variable"}:
				frappe.throw(_("Select a valid cost nature for row {0}.").format(row.idx))
