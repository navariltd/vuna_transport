# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from vuna_transport.services.costing.transport_cost_estimation_service import (
	estimate_transport_cost_for_loads,
)
from vuna_transport.services.fleet.vehicle_allocation_service import (
	activate_allocation,
	get_allocation_load_names,
	restore_previous_allocation,
	sync_allocation_execution_status,
	validate_allocation,
	validate_allocation_cancellation,
)


class VehicleAllocation(Document):
	def validate(self):
		validate_allocation(self)
		from vuna_transport.services.fleet.allocation_cargo_service import collect_load_items
		from vuna_transport.services.fleet.vehicle_allocation_service import get_allocation_loads

		self.set("loaded_items", collect_load_items(get_allocation_loads(self)))
		from vuna_transport.services.fleet.vehicle_incident_service import (
			validate_replacement_allocation_incident,
		)

		validate_replacement_allocation_incident(self)

	def after_insert(self):
		from vuna_transport.services.fleet.vehicle_incident_service import (
			link_replacement_allocation_to_incident,
		)

		link_replacement_allocation_to_incident(self)

	def before_submit(self):
		self.status = "Allocated"
		self.effective_from = self.effective_from or now_datetime()
		snapshot_transport_cost(self)

	def on_submit(self):
		activate_allocation(self)
		sync_allocation_execution_status(self)

	def before_cancel(self):
		validate_allocation_cancellation(self)
		from vuna_transport.services.fleet.vehicle_allocation_service import (
			prepare_allocation_cancellation,
		)

		prepare_allocation_cancellation(self)

	def on_cancel(self):
		self.db_set("status", "Cancelled", update_modified=False)
		restore_previous_allocation(self, active_link_cleared=True)

	@frappe.whitelist()
	def create_delivery_notes(self):
		self.check_permission("write")
		if self.docstatus != 1:
			frappe.throw("Submit the Vehicle Allocation before creating Delivery Notes.")

		from vuna_transport.services.integrations.erpnext_integration import (
			create_delivery_notes_for_allocation,
		)

		return create_delivery_notes_for_allocation(self)

	@frappe.whitelist()
	def create_delivery_trip(self):
		self.check_permission("write")
		if self.docstatus != 1:
			frappe.throw("Submit the Vehicle Allocation before creating a Delivery Trip.")

		load_names = get_allocation_load_names(self)
		if not load_names:
			frappe.throw(_("Add at least one Transport Load before creating a Delivery Trip."))

		from vuna_transport.services.integrations.erpnext_integration import create_delivery_trip_for_load

		return create_delivery_trip_for_load(load_names[0]).name


def snapshot_transport_cost(allocation):
	"""Persist the estimate used when an allocation becomes confirmed."""
	if not frappe.db.get_single_value("Transport Settings", "enable_transport_costing"):
		return
	load_names = get_allocation_load_names(allocation)
	if not allocation.vehicle or not load_names:
		return
	result = estimate_transport_cost_for_loads(load_names, allocation.vehicle)
	allocation.cost_structure_assignment = result.get("cost_structure_assignment")
	allocation.cost_structure = result.get("cost_structure")
	allocation.estimated_distance = result.get("distance")
	allocation.estimated_distance_uom = result.get("distance_uom")
	allocation.distance_source = result.get("distance_source")
	allocation.estimated_marginal_cost = result.get("marginal_cost")
	allocation.estimated_fixed_cost = result.get("fixed_cost")
	allocation.estimated_full_cost = result.get("estimated_full_cost")
	allocation.estimated_cost_currency = result.get("currency")
	allocation.estimated_on = now_datetime()
	allocation.cost_breakdown = result.get("components") or []
	allocation.cost_warnings = "\n".join(result.get("warnings") or [])
