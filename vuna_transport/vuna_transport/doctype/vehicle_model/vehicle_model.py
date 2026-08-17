# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class VehicleModel(Document):
	def validate(self):
		settings = frappe.get_cached_doc("Transport Settings")
		if not self.weight_uom:
			self.weight_uom = settings.default_weight_uom
		if not self.volume_uom:
			self.volume_uom = settings.default_volume_uom
