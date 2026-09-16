# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from vuna_transport.services.shared.measurement_service import convert_measurement


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
		if flt(getattr(self, "minimum_payload_weight", 0)):
			self.minimum_payload_weight_uom = (
				self.minimum_payload_weight_uom or self.max_payload_weight_uom or settings.default_weight_uom
			)
			minimum_weight = convert_measurement(
				self.minimum_payload_weight,
				self.minimum_payload_weight_uom,
				self.max_payload_weight_uom,
				_("minimum payload weight"),
			)
			if minimum_weight > flt(self.max_payload_weight):
				frappe.throw(_("Minimum Payload Weight cannot exceed Maximum Payload Weight."))
		if flt(getattr(self, "minimum_volume", 0)):
			self.minimum_volume_uom = self.minimum_volume_uom or self.max_volume_uom or settings.default_volume_uom
			minimum_volume = convert_measurement(
				self.minimum_volume,
				self.minimum_volume_uom,
				self.max_volume_uom,
				_("minimum volume"),
			)
			if minimum_volume > flt(self.max_volume):
				frappe.throw(_("Minimum Volume cannot exceed Maximum Volume."))
