# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from vuna_transport.services.fleet.vehicle_incident_service import validate_incident


class VehicleIncident(Document):
	def validate(self):
		validate_incident(self)
