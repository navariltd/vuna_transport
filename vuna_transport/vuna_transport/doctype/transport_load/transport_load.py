# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from vuna_transport.services.execution.dispatch_guard_service import (
	set_delivery_load_status,
	validate_load_can_be_cancelled,
)
from vuna_transport.services.execution.execution_service import get_load_execution_summary
from vuna_transport.services.integrations.erpnext_integration import (
	create_delivery_notes_for_load,
	create_delivery_trip_for_load,
	create_pick_list_for_load,
)
from vuna_transport.services.planning.delivery_load_service import (
	add_delivery_orders_to_load,
	calculate_load_totals,
	recommend_vehicle_category,
	sync_delivery_order_allocations,
	validate_capacity,
	validate_load_allocations,
)
from vuna_transport.services.planning.demand_lifecycle_service import lock_demands, sync_demand_allocations
from vuna_transport.services.planning.transport_load_service import (
	is_demand_load,
	validate_demand_allocations,
)
from vuna_transport.services.routing.distance_estimation_service import (
	apply_delivery_load_distance_estimate,
)
from vuna_transport.services.routing.route_service import apply_route_defaults


class TransportLoad(Document):
	def validate(self):
		_validate_source_warehouse_change(self)
		if is_demand_load(self):
			validate_demand_allocations(self)
			validate_capacity(self)
			return
		apply_route_defaults(self)
		apply_delivery_load_distance_estimate(self)
		if self.delivery_orders:
			validate_load_allocations(self)
		calculate_load_totals(self)
		validate_capacity(self)

	def before_submit(self):
		if is_demand_load(self):
			validate_demand_allocations(self)
			validate_capacity(self)
			if not self.required_vehicle_category:
				self.required_vehicle_category = recommend_vehicle_category(self)
			self.status = "Approved"
			return
		if not self.delivery_orders:
			frappe.throw(_("Add at least one Delivery Order using Get Items before submitting the load."))
		validate_load_allocations(self)
		calculate_load_totals(self)
		validate_capacity(self)
		if not self.required_vehicle_category:
			self.required_vehicle_category = recommend_vehicle_category(self)
		self.status = "Approved"

	def on_submit(self):
		sync_demand_allocations(self.get_affected_demand_names())
		if self.get_delivery_order_names():
			sync_delivery_order_allocations(self.get_delivery_order_names())

	def before_cancel(self):
		lock_demands(self.get_affected_demand_names())
		validate_load_can_be_cancelled(self)

	def on_cancel(self):
		set_delivery_load_status(self, "Cancelled", _("Transport Load cancelled"), force=True)
		sync_demand_allocations(self.get_affected_demand_names())
		if self.get_delivery_order_names():
			sync_delivery_order_allocations(self.get_delivery_order_names())

	def on_update(self):
		sync_demand_allocations(self.get_affected_demand_names())
		if self.docstatus == 0 and self.get_affected_delivery_order_names():
			sync_delivery_order_allocations(self.get_affected_delivery_order_names())

	def after_delete(self):
		sync_demand_allocations(self.get_affected_demand_names())
		if self.get_delivery_order_names():
			sync_delivery_order_allocations(self.get_delivery_order_names())

	def get_delivery_order_names(self):
		return {row.delivery_order for row in self.delivery_orders if row.delivery_order}

	def on_trash(self):
		lock_demands(self.get_affected_demand_names())

	def get_affected_demand_names(self):
		names = {row.get("transport_demand") for row in self.delivery_orders if row.get("transport_demand")}
		previous = self.get_doc_before_save()
		if previous:
			names.update(
				row.get("transport_demand") for row in previous.delivery_orders if row.get("transport_demand")
			)
		return names

	def get_affected_delivery_order_names(self):
		names = self.get_delivery_order_names()
		previous = self.get_doc_before_save()
		if previous:
			names.update(row.delivery_order for row in previous.delivery_orders if row.delivery_order)
		return names

	@frappe.whitelist()
	def create_pick_list(self):
		self.check_permission("write")
		self._require_delivery_execution()
		return create_pick_list_for_load(self).name

	@frappe.whitelist()
	def create_delivery_notes(self):
		self.check_permission("write")
		self._require_delivery_execution()
		return create_delivery_notes_for_load(self)

	@frappe.whitelist()
	def create_delivery_trip(self):
		self.check_permission("write")
		return create_delivery_trip_for_load(self)

	@frappe.whitelist()
	def get_items(self, delivery_order_names):
		self.check_permission("write")
		return add_delivery_orders_to_load(self, delivery_order_names)

	@frappe.whitelist()
	def get_execution_summary(self):
		self.check_permission("read")
		return get_load_execution_summary(self)

	def _require_delivery_execution(self):
		if is_demand_load(self):
			frappe.throw(
				_("Warehouse transfer loads cannot create customer Delivery Notes or delivery-only trips.")
			)


def _validate_source_warehouse_change(load):
	"""Allow dispatch-origin changes only before stock execution has started."""
	previous = load.get_doc_before_save()
	if not previous or previous.source_warehouse == load.source_warehouse:
		return
	if load.docstatus != 1:
		return
	if load.pick_list and frappe.db.get_value("Pick List", load.pick_list, "docstatus") != 2:
		frappe.throw(_("Cancel the Pick List before changing the Transport Load dispatch warehouse."))
	if frappe.db.exists(
		"Delivery Note",
		{"vuna_delivery_load": load.name, "docstatus": ["!=", 2]},
	):
		frappe.throw(_("The Transport Load dispatch warehouse cannot change after Delivery Notes exist."))
	if load.delivery_trip:
		frappe.throw(_("The Transport Load dispatch warehouse cannot change after a Delivery Trip exists."))
