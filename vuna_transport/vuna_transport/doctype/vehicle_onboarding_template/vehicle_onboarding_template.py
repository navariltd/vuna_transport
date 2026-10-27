# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from vuna_transport.services.fleet.vehicle_owner_service import normalize_vehicle_owner


class VehicleOnboardingTemplate(Document):
	def validate(self):
		structure = frappe.get_cached_doc("Transport Cost Structure", self.transport_cost_structure)
		if structure.docstatus != 1 or structure.disabled:
			frappe.throw(_("Select a submitted, enabled Transport Cost Structure."))
		self.company = structure.company
		self.vehicle_owner = normalize_vehicle_owner(structure.vehicle_owner)

		seen = set()
		for row in self.documents:
			if not row.vehicle_document_type:
				continue
			if row.vehicle_document_type in seen:
				frappe.throw(
					_("Vehicle Document Type {0} cannot be repeated.").format(row.vehicle_document_type)
				)
			seen.add(row.vehicle_document_type)
