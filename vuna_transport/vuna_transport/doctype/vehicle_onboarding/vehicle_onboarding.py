# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate

from vuna_transport.services.fleet.license_plate_service import normalize_and_validate_license_plate
from vuna_transport.services.fleet.vehicle_owner_service import normalize_vehicle_owner
from vuna_transport.services.shared.security_service import require_doctype_permission


class VehicleOnboarding(Document):
	def validate(self):
		self.status = self.status or "Draft"
		self._validate_license_plate()
		self._set_category_model()
		self._validate_owner()
		self._validate_dates()
		self._validate_required_documents()

	def before_submit(self):
		self._validate_required_documents()

	def on_submit(self):
		vehicle = self._create_vehicle()
		self.db_set("vehicle", vehicle.name, update_modified=False)
		self.db_set("status", "Approved", update_modified=False)

	def _validate_owner(self):
		owner = normalize_vehicle_owner(self.vehicle_owner)
		if owner == "Supplier" and not self.supplier:
			frappe.throw(_("Supplier is required for a supplier-owned vehicle."))
		if owner == "Company" and self.supplier:
			frappe.throw(_("Supplier must be empty for a company-owned vehicle."))

	def _validate_license_plate(self):
		self.license_plate = normalize_and_validate_license_plate(self.license_plate, self.vehicle_category)
		existing_vehicle = frappe.db.get_value("Vehicle", {"license_plate": self.license_plate}, "name")
		if existing_vehicle and existing_vehicle != self.vehicle:
			frappe.throw(
				_("Vehicle {0} already exists for License Plate {1}.").format(
					existing_vehicle, self.license_plate
				)
			)
		existing_onboarding = frappe.db.get_value(
			"Vehicle Onboarding",
			{"license_plate": self.license_plate, "name": ["!=", self.name or ""]},
			"name",
		)
		if existing_onboarding:
			frappe.throw(
				_("Vehicle Onboarding {0} already uses License Plate {1}.").format(
					existing_onboarding, self.license_plate
				)
			)

	def _set_category_model(self):
		if not self.vehicle_category or self.vehicle_model:
			return
		self.vehicle_model = frappe.db.get_value("Vehicle Category", self.vehicle_category, "vehicle_model")
		if not self.vehicle_model:
			frappe.throw(
				_("Select a Vehicle Model because Vehicle Category {0} has no default model.").format(
					self.vehicle_category
				)
			)

	def _validate_dates(self):
		if self.insurance_start_date and self.insurance_end_date:
			if getdate(self.insurance_start_date) > getdate(self.insurance_end_date):
				frappe.throw(_("Insurance End Date must be on or after Insurance Start Date."))

	def _validate_required_documents(self):
		requirements = frappe.get_all(
			"Vehicle Document Type",
			filters={"disabled": 0, "mandatory_for_dispatch": 1},
			fields=["name", "document_type_name", "company", "vehicle_category", "vehicle_owner"],
		)
		owner = normalize_vehicle_owner(self.vehicle_owner)
		rows = {row.document_type for row in self.documents if row.document_type}
		for requirement in requirements:
			if requirement.company and requirement.company != self.company:
				continue
			if requirement.vehicle_category and requirement.vehicle_category != self.vehicle_category:
				continue
			if requirement.vehicle_owner and normalize_vehicle_owner(requirement.vehicle_owner) != owner:
				continue
			if (requirement.document_type_name or requirement.name).strip().lower() == "insurance":
				if not self.policy_no or not self.insurance_end_date:
					frappe.throw(_("Mandatory Vehicle insurance details are required before approval."))
			elif requirement.name not in rows:
				frappe.throw(_("Mandatory Vehicle Document {0} is missing.").format(requirement.name))
		self._validate_template_documents(rows)

	def _validate_template_documents(self, document_types):
		if not self.transport_cost_structure:
			return
		template = frappe.db.get_value(
			"Vehicle Onboarding Template",
			{"transport_cost_structure": self.transport_cost_structure},
			"name",
		)
		if not template:
			return
		required_documents = frappe.get_all(
			"Vehicle Onboarding Template Document",
			filters={"parent": template, "mandatory": 1},
			pluck="vehicle_document_type",
		)
		missing = sorted(set(required_documents) - document_types)
		if missing:
			frappe.throw(_("Onboarding Template requires: {0}.").format(", ".join(missing)))

	def _create_vehicle(self):
		if self.vehicle:
			return frappe.get_doc("Vehicle", self.vehicle)
		self._validate_license_plate()

		model = frappe.get_cached_doc("Vehicle Model", self.vehicle_model)
		vehicle = frappe.get_doc(
			{
				"doctype": "Vehicle",
				"license_plate": self.license_plate,
				"company": self.company,
				"make": model.manufacturer,
				"model": model.model_name,
				"last_odometer": 0,
				"vuna_transport_enabled": 1,
				"vuna_vehicle_owner": self.vehicle_owner,
				"vuna_supplier": self.supplier,
				"vuna_vehicle_category": self.vehicle_category,
				"vuna_body_configuration": self.body_configuration,
				"vuna_refrigerated": self.refrigerated,
				"vuna_vehicle_model": self.vehicle_model,
				"vuna_transport_cost_structure": self.transport_cost_structure,
				"fuel_type": self.fuel_type,
				"uom": self.fuel_uom,
				"vuna_asset": self.asset,
				"insurance_company": self.insurance_company,
				"policy_no": self.policy_no,
				"start_date": self.insurance_start_date,
				"end_date": self.insurance_end_date,
				"vuna_onboarding": self.name,
			}
		)
		vehicle.insert()
		for row in self.documents:
			if row.document_type:
				frappe.get_doc(
					{
						"doctype": "Vehicle Document",
						"vehicle": vehicle.name,
						"document_type": row.document_type,
						"document_number": row.document_number,
						"issued_by": row.issued_by,
						"issue_date": row.issue_date,
						"expiry_date": row.expiry_date,
						"attachment": row.attachment,
						"remarks": row.remarks,
					}
				).insert()
		return vehicle


@frappe.whitelist()
def get_onboarding_template_documents(transport_cost_structure):
	"""Return the configured checklist for a submitted cost structure."""
	require_doctype_permission("Vehicle Onboarding Template", "read")
	if not transport_cost_structure:
		return []
	template = frappe.db.get_value(
		"Vehicle Onboarding Template",
		{"transport_cost_structure": transport_cost_structure},
		"name",
	)
	if not template:
		return []
	return frappe.get_all(
		"Vehicle Onboarding Template Document",
		filters={"parent": template},
		fields=["vehicle_document_type", "mandatory"],
		order_by="idx",
	)
