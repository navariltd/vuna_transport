# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from vuna_transport.services.routing.location_service import validate_reference_coordinates


class County(Document):
	def validate(self):
		validate_reference_coordinates(self)
		if not self.territory:
			return
		if frappe.db.get_value("Territory", self.territory, "is_group"):
			frappe.throw(_("County must link to a non-group Territory."))
