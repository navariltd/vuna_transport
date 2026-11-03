# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from vuna_transport.services.planning.delivery_load_service import create_delivery_load
from vuna_transport.services.routing.location_service import set_delivery_order_location_snapshots
from vuna_transport.services.shared.measurement_service import get_transport_uoms
from vuna_transport.services.shared.security_service import require_doctype_permission


class DeliveryOrder(Document):
	def before_insert(self):
		set_delivery_order_location_snapshots(self)

	def validate(self):
		self.sync_quantities_from_sales_order()
		self.validate_items()
		self.validate_delivery_group()
		self.calculate_totals()
		self.validate_allocation()

	def validate_delivery_group(self):
		groups = {row.delivery_group or None for row in self.items}
		if len(groups) > 1:
			frappe.throw(_("All rows in a Delivery Order must have the same Delivery Group."))
		row_group = next(iter(groups), None)
		if self.delivery_group and row_group and self.delivery_group != row_group:
			frappe.throw(_("The Delivery Order Delivery Group must match its item rows."))
		if row_group and not self.delivery_group:
			self.delivery_group = row_group
		if self.delivery_group and not row_group:
			frappe.throw(_("The Delivery Order Delivery Group must match its item rows."))
		if self.delivery_group and frappe.db.get_value("Delivery Group", self.delivery_group, "disabled"):
			if self.is_new() or self.get_doc_before_save() is None:
				frappe.throw(
					_(
						"Delivery Group {0} is disabled and cannot be assigned to a new Delivery Order."
					).format(self.delivery_group)
				)

	def validate_items(self):
		if not self.items:
			frappe.throw(_("At least one Delivery Order Item is required."))

		for item in self.items:
			if flt(item.transport_qty) <= 0 and self.is_new():
				frappe.throw(
					_("Transport quantity must be greater than zero for item {0}.").format(item.item_code)
				)
			if flt(item.ordered_qty) < flt(item.transport_qty):
				frappe.throw(
					_("Transport quantity cannot exceed ordered quantity for item {0}.").format(
						item.item_code
					)
				)

	def sync_quantities_from_sales_order(self):
		"""Keep generated quantities and measurements authoritative from Sales Order."""
		from vuna_transport.services.demand.delivery_order_service import (
			sync_delivery_order_quantities,
		)

		if self.is_new() or not self.sales_order:
			return
		sync_delivery_order_quantities(self, persist=False)

	def calculate_totals(self):
		uoms = get_transport_uoms()
		self.weight_uom = uoms.weight_uom or next(
			(item.weight_uom for item in self.items if item.weight_uom), None
		)
		self.volume_uom = uoms.volume_uom or next(
			(item.volume_uom for item in self.items if item.volume_uom), None
		)
		self.total_quantity = sum(flt(item.transport_qty) for item in self.items)
		self.requested_quantity = self.total_quantity
		self.total_weight = sum(flt(item.weight) for item in self.items)
		self.total_volume = sum(flt(item.volume) for item in self.items)
		self.total_pallets = sum(flt(item.pallets) for item in self.items)
		self.total_cases = sum(flt(item.cases) for item in self.items)
		self.total_crates = sum(flt(item.crates) for item in self.items)
		self.unallocated_quantity = max(
			flt(self.requested_quantity) - flt(self.allocated_quantity) - flt(self.reserved_quantity),
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
		require_doctype_permission("Transport Load", "create")
		return create_delivery_load([self.name])
