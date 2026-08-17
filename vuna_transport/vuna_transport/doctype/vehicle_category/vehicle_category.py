# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class VehicleCategory(Document):
	def validate(self):
		settings = frappe.get_cached_doc("Transport Settings")
		if not self.max_payload_weight_uom:
			self.max_payload_weight_uom = settings.default_weight_uom
		if not self.max_volume_uom:
			self.max_volume_uom = settings.default_volume_uom
