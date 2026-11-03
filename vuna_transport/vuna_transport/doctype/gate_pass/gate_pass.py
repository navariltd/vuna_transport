"""Gate authorization for physical vehicle movement."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from vuna_transport.services.execution.lineage_service import get_delivery_trip_load_names
from vuna_transport.services.fleet.vehicle_allocation_service import get_allocation_load_names


class GatePass(Document):
	def validate(self):
		self._validate_references()
		self._populate_execution_context()
		if self.direction == "Outbound":
			self._validate_outbound_documents()
		if (
			self.gate_in_datetime
			and self.gate_out_datetime
			and self.gate_in_datetime < self.gate_out_datetime
		):
			frappe.throw(_("Gate In cannot be earlier than Gate Out."))

	def before_submit(self):
		self._validate_references()
		self._populate_execution_context()
		if self.direction == "Outbound":
			self._validate_outbound_documents()
		self.status = "Issued"
		self.issued_on = self.issued_on or now_datetime()
		self.issued_by = self.issued_by or frappe.session.user

	def on_cancel(self):
		self.db_set("status", "Cancelled", update_modified=False)

	@frappe.whitelist()
	def mark_gate_out(self):
		self.check_permission("write")
		if self.docstatus != 1:
			frappe.throw(_("Submit the Gate Pass before recording Gate Out."))
		if self.status != "Issued":
			frappe.throw(_("Only an Issued Gate Pass can be marked as Gate Out."))
		self.db_set(
			{"status": "Exited", "gate_out_datetime": self.gate_out_datetime or now_datetime()},
			update_modified=False,
		)
		self.status = "Exited"
		return self

	@frappe.whitelist()
	def mark_gate_in(self):
		self.check_permission("write")
		if self.docstatus != 1:
			frappe.throw(_("Submit the Gate Pass before recording Gate In."))
		if self.status != "Exited":
			frappe.throw(_("Mark the Gate Pass as Gate Out before recording Gate In."))
		self.db_set(
			{"status": "Returned", "gate_in_datetime": self.gate_in_datetime or now_datetime()},
			update_modified=False,
		)
		self.status = "Returned"
		return self

	def _validate_references(self):
		if not self.delivery_trip:
			frappe.throw(_("Create the Gate Pass from a Delivery Trip."))

		if self.delivery_load:
			load = frappe.get_doc("Transport Load", self.delivery_load)
			if load.docstatus != 1:
				frappe.throw(
					_("Transport Load {0} must be submitted before issuing a Gate Pass.").format(load.name)
				)
			if self.company and load.company != self.company:
				frappe.throw(_("Gate Pass and Transport Load must belong to the same Company."))

		if self.vehicle_allocation:
			allocation = frappe.get_doc("Vehicle Allocation", self.vehicle_allocation)
			if allocation.docstatus != 1 or allocation.status not in ("Allocated", "Accepted", "Replaced"):
				frappe.throw(
					_("Vehicle Allocation {0} must be submitted and active.").format(allocation.name)
				)
			if self.company and allocation.company != self.company:
				frappe.throw(_("Gate Pass and Vehicle Allocation must belong to the same Company."))

		if self.delivery_trip:
			trip = frappe.get_doc("Delivery Trip", self.delivery_trip)
			if trip.docstatus == 2:
				frappe.throw(
					_("Cancelled Delivery Trip {0} cannot be used for a Gate Pass.").format(trip.name)
				)
			if self.company and trip.company != self.company:
				frappe.throw(_("Gate Pass and Delivery Trip must belong to the same Company."))
			trip_load_names = get_delivery_trip_load_names(trip)
			if self.delivery_load and trip_load_names and self.delivery_load not in trip_load_names:
				frappe.throw(
					_("Transport Load {0} is not part of Delivery Trip {1}.").format(
						self.delivery_load, trip.name
					)
				)
			if self.vehicle_allocation:
				allocation_load_names = set(
					get_allocation_load_names(frappe.get_doc("Vehicle Allocation", self.vehicle_allocation))
				)
				if trip_load_names and not trip_load_names.issubset(allocation_load_names):
					frappe.throw(
						_(
							"Vehicle Allocation {0} does not contain every Transport Load on Delivery Trip {1}."
						).format(self.vehicle_allocation, trip.name)
					)

	def _populate_execution_context(self):
		if self.delivery_trip:
			trip = frappe.get_doc("Delivery Trip", self.delivery_trip)
			self.company = self.company or trip.company
			self.vehicle = self.vehicle or trip.vehicle
			self.driver = self.driver or trip.driver
			self.vehicle_allocation = (
				self.vehicle_allocation
				or getattr(trip, "vuna_current_vehicle_allocation", None)
				or getattr(trip, "vuna_vehicle_allocation", None)
			)
			trip_load_names = get_delivery_trip_load_names(trip)
			if not self.delivery_load and len(trip_load_names) == 1:
				self.delivery_load = trip_load_names[0]

		if self.delivery_load:
			load = frappe.get_doc("Transport Load", self.delivery_load)
			self.company = self.company or load.company
			if load.active_vehicle_allocation and not self.vehicle_allocation:
				self.vehicle_allocation = load.active_vehicle_allocation

		if not self.vehicle or not self.driver:
			frappe.throw(_("A Gate Pass requires a Vehicle and Driver."))

	def _validate_outbound_documents(self):
		load_names = set(get_delivery_trip_load_names(frappe.get_doc("Delivery Trip", self.delivery_trip)))
		if not load_names:
			frappe.throw(_("The Delivery Trip must contain at least one Transport Load."))
		delivery_notes = frappe.get_all(
			"Delivery Note",
			filters={"vuna_delivery_load": ["in", sorted(load_names)], "docstatus": 1},
			pluck="name",
		)
		if not delivery_notes:
			frappe.throw(_("Submit at least one Delivery Note before issuing an outbound Gate Pass."))
