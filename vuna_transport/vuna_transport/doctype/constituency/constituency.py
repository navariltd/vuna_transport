# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from vuna_transport.services.routing.location_service import validate_reference_coordinates


class Constituency(Document):
	def validate(self):
		validate_reference_coordinates(self)
		if self.county and frappe.db.get_value("County", self.county, "disabled"):
			frappe.throw(_("Constituency cannot belong to a disabled County."))
