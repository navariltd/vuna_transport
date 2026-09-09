# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class TransportCostingModel(Document):
	def validate(self):
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
			if row.cost_nature not in {"Fixed", "Variable", "Accessorial / Actual"}:
				frappe.throw(_("Select a valid cost nature for row {0}.").format(row.idx))
