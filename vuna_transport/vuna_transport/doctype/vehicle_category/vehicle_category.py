# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class VehicleCategory(Document):
	def validate(self):
		override_percentage = flt(getattr(self, "capacity_override_percentage", 0))
		if override_percentage < 0 or override_percentage > 100:
			frappe.throw(_("Capacity Override Percentage must be between 0 and 100."))
		settings = frappe.get_cached_doc("Transport Settings")
		if override_percentage and not settings.allow_capacity_override:
			frappe.throw(
				_(
					"Enable Allow Capacity Override in Transport Settings before configuring a Vehicle Category override."
				)
			)
		if not self.max_payload_weight_uom:
			self.max_payload_weight_uom = settings.default_weight_uom
		if not self.max_volume_uom:
			self.max_volume_uom = settings.default_volume_uom
