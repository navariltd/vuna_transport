# Copyright (c) 2026, Vuna Transport contributors and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


ALLOWED_FILTER_KEYS = {
	"delivery_group",
	"transport_compatibility_group",
	"preferred_route",
	"maximum_vehicle_category",
	"delivery_address",
	"territory",
	"county",
	"constituency",
	"town",
	"transport_zone",
	"delivery_method",
	"priority",
	"customer",
	"source_warehouse",
	"transport_handling_profile",
}


class DispatchPlanningFilter(Document):
	def validate(self):
		if self.field_key not in ALLOWED_FILTER_KEYS:
			frappe.throw(_("Filter field {0} is not supported.").format(self.field_key))
		if self.disabled:
			return
		duplicate = frappe.db.exists(
			"Dispatch Planning Filter",
			{
				"field_key": self.field_key,
				"disabled": 0,
				"name": ["!=", self.name],
			},
		)
		if duplicate:
			frappe.throw(
				_("An active Dispatch Planning Filter for {0} already exists.").format(self.field_key)
			)
