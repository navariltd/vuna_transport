# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime


class VehicleAvailabilityBlock(Document):
	def validate(self):
		self._set_and_validate_company()
		if not self.from_datetime or not self.to_datetime:
			return
		if get_datetime(self.from_datetime) >= get_datetime(self.to_datetime):
			frappe.throw(_("To Datetime must be after From Datetime."))

	def _set_and_validate_company(self):
		if not self.vehicle:
			return
		vehicle_company = frappe.db.get_value("Vehicle", self.vehicle, "company")
		if self.company and vehicle_company and self.company != vehicle_company:
			frappe.throw(_("Vehicle {0} belongs to Company {1}.").format(self.vehicle, vehicle_company))
		self.company = vehicle_company or self.company
