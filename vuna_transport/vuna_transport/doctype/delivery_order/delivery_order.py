# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt
from frappe.model.document import Document

from vuna_transport.services.shared.measurement_service import get_transport_uoms

from vuna_transport.services.planning.delivery_load_service import create_delivery_load
from vuna_transport.services.routing.location_service import set_delivery_order_location_snapshots
from vuna_transport.services.shared.security_service import require_doctype_permission


class DeliveryOrder(Document):
	def before_insert(self):
		set_delivery_order_location_snapshots(self)

	def validate(self):
		self.validate_items()
		self.calculate_totals()
		self.validate_allocation()

	def validate_items(self):
		if not self.items:
			frappe.throw(_("At least one Delivery Order Item is required."))

		for item in self.items:
			if flt(item.transport_qty) <= 0:
				frappe.throw(_("Transport quantity must be greater than zero for item {0}.").format(item.item_code))
			if flt(item.ordered_qty) < flt(item.transport_qty):
				frappe.throw(
					_("Transport quantity cannot exceed ordered quantity for item {0}.").format(item.item_code)
				)

	def calculate_totals(self):
		uoms = get_transport_uoms()
		self.weight_uom = uoms.weight_uom or next((item.weight_uom for item in self.items if item.weight_uom), None)
		self.volume_uom = uoms.volume_uom or next((item.volume_uom for item in self.items if item.volume_uom), None)
		self.total_quantity = sum(flt(item.transport_qty) for item in self.items)
		self.requested_quantity = self.total_quantity
		self.total_weight = sum(flt(item.weight) for item in self.items)
		self.total_volume = sum(flt(item.volume) for item in self.items)
		self.total_pallets = sum(flt(item.pallets) for item in self.items)
		self.total_cases = sum(flt(item.cases) for item in self.items)
		self.total_crates = sum(flt(item.crates) for item in self.items)
		self.unallocated_quantity = max(
			flt(self.requested_quantity)
			- flt(self.allocated_quantity)
			- flt(self.reserved_quantity),
			0,
		)

	def validate_allocation(self):
		if flt(self.allocated_quantity) < 0:
			frappe.throw(_("Allocated quantity cannot be negative."))
		if flt(self.reserved_quantity) < 0:
			frappe.throw(_("Reserved quantity cannot be negative."))
		if flt(self.allocated_quantity) + flt(self.reserved_quantity) > flt(self.requested_quantity):
			frappe.throw(_("Allocated and reserved quantities cannot exceed requested quantity."))

	@frappe.whitelist()
	def create_load(self):
		self.check_permission("read")
		require_doctype_permission("Delivery Load", "create")
		return create_delivery_load([self.name])
