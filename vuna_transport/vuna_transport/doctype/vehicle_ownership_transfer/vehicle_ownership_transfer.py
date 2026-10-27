# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, today

from vuna_transport.services.fleet.vehicle_owner_service import normalize_vehicle_owner


class VehicleOwnershipTransfer(Document):
	def validate(self):
		self._validate_effective_date()
		vehicle = frappe.get_cached_doc("Vehicle", self.vehicle)
		self.company = vehicle.company
		self.current_owner = vehicle.vuna_vehicle_owner
		self.current_supplier = vehicle.vuna_supplier
		self._validate_new_owner(vehicle)

	def before_submit(self):
		self._validate_current_owner()

	def on_submit(self):
		new_owner = normalize_vehicle_owner(self.new_owner)
		frappe.db.set_value(
			"Vehicle",
			self.vehicle,
			{
				"vuna_vehicle_owner": new_owner,
				"vuna_supplier": self.new_supplier if new_owner == "Supplier" else None,
			},
			update_modified=True,
		)
		frappe.clear_document_cache("Vehicle", self.vehicle)
		from vuna_transport.services.fleet.fleet_service import sync_vehicle_operational_snapshots

		sync_vehicle_operational_snapshots([self.vehicle])
		self.db_set("status", "Submitted", update_modified=False)

	def _validate_effective_date(self):
		if self.effective_date and getdate(self.effective_date) < getdate(today()):
			frappe.throw(_("Effective Date cannot be before today."))

	def _validate_new_owner(self, vehicle):
		if normalize_vehicle_owner(self.new_owner) == "Supplier" and not self.new_supplier:
			frappe.throw(_("New Supplier is required for a supplier-owned vehicle."))
		if normalize_vehicle_owner(self.new_owner) == "Company" and self.new_supplier:
			frappe.throw(_("New Supplier must be empty for a company-owned vehicle."))
		if normalize_vehicle_owner(self.new_owner) == "Supplier" and getattr(vehicle, "vuna_asset", None):
			frappe.throw(
				_("Handle the linked ERPNext Asset before transferring this Vehicle to supplier ownership.")
			)

	def _validate_current_owner(self):
		current = frappe.db.get_value(
			"Vehicle",
			self.vehicle,
			["vuna_vehicle_owner", "vuna_supplier"],
			as_dict=True,
		)
		if not current:
			frappe.throw(_("Vehicle {0} no longer exists.").format(self.vehicle))
		if current.vuna_vehicle_owner != self.current_owner or current.vuna_supplier != self.current_supplier:
			frappe.throw(
				_(
					"The Vehicle owner changed after this transfer was prepared. Refresh and create a new transfer."
				)
			)
