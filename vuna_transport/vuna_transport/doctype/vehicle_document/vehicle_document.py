# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


class VehicleDocument(Document):
	def validate(self):
		self._set_and_validate_company()
		self._validate_dates()
		self._validate_document_type()

	def _set_and_validate_company(self):
		if not self.vehicle:
			return
		vehicle_company = frappe.db.get_value("Vehicle", self.vehicle, "company")
		if self.company and vehicle_company and self.company != vehicle_company:
			frappe.throw(
				_("Vehicle {0} belongs to Company {1}.").format(
					self.vehicle, vehicle_company
				)
			)
		self.company = vehicle_company or self.company

	def _validate_dates(self):
		if self.issue_date and self.expiry_date:
			if getdate(self.issue_date) > getdate(self.expiry_date):
				frappe.throw(_("Expiry Date must be on or after Issue Date."))

	def _validate_document_type(self):
		if not self.document_type or not self.vehicle:
			return
		document_type = frappe.get_cached_doc(
			"Vehicle Document Type", self.document_type
		)
		if document_type.disabled:
			frappe.throw(
				_("Vehicle Document Type {0} is disabled.").format(document_type.name)
			)
		if document_type.company and self.company != document_type.company:
			frappe.throw(
				_("Vehicle Document Type {0} applies to Company {1}.").format(
					document_type.name, document_type.company
				)
			)
		if document_type.document_type_name.strip().lower() == "insurance":
			frappe.throw(
				_(
					"Use the standard insurance fields on Vehicle for Insurance; do not create a duplicate Vehicle Document."
				)
			)
		vehicle = frappe.db.get_value(
			"Vehicle",
			self.vehicle,
			["vuna_vehicle_category", "vuna_ownership_type"],
			as_dict=True,
		)
		if (
			document_type.vehicle_category
			and document_type.vehicle_category != vehicle.vuna_vehicle_category
		):
			frappe.throw(
				_("Vehicle Document Type {0} applies to Vehicle Category {1}.").format(
					document_type.name, document_type.vehicle_category
				)
			)
		if (
			document_type.ownership_type
			and document_type.ownership_type != vehicle.vuna_ownership_type
		):
			frappe.throw(
				_("Vehicle Document Type {0} applies to {1} Vehicles.").format(
					document_type.name, document_type.ownership_type
				)
			)
		if document_type.requires_expiry_date and not self.expiry_date:
			frappe.throw(
				_("Expiry Date is required for {0}.").format(document_type.name)
			)
