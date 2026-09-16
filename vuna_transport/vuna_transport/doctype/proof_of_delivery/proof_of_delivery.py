# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from vuna_transport.services.execution.delivery_exception_service import (
	cancel_delivery_exception_from_pod,
	sync_delivery_exception_from_pod,
)
from vuna_transport.services.execution.pod_service import (
	derive_pod_status,
	reset_delivery_stop_from_pod,
	update_delivery_stop_from_pod,
	validate_pod_items,
)


class ProofofDelivery(Document):
	def validate(self):
		self.populate_stop_details()
		self.populate_item_details()
		validate_pod_items(self.items, self.rejection_reasons)
		self.total_accepted_qty = sum(flt(item.delivered_qty) for item in self.items)
		self.total_rejected_qty = sum(flt(item.rejected_qty) for item in self.items)
		self.status = derive_pod_status(self.items)
		self.validate_exception_reason()

	def populate_stop_details(self):
		stop = frappe.db.get_value(
			"Delivery Stop",
			self.delivery_stop,
			[
				"parent",
				"parenttype",
				"customer",
				"address",
				"delivery_note",
				"vuna_delivery_order",
				"vuna_delivery_load",
			],
			as_dict=True,
		)
		if not stop or stop.parenttype != "Delivery Trip" or stop.parent != self.delivery_trip:
			frappe.throw(_("The selected Delivery Stop does not belong to the selected Delivery Trip."))
		self.delivery_order = stop.vuna_delivery_order
		self.customer = stop.customer
		self.delivery_address = stop.address
		self.delivery_note = stop.delivery_note
		self.delivery_load = stop.vuna_delivery_load or frappe.db.get_value(
			"Delivery Note", self.delivery_note, "vuna_delivery_load"
		)

	def populate_item_details(self):
		"""Populate item snapshots with one batched query instead of one query per row."""
		item_names = {item.delivery_order_item for item in self.items if item.delivery_order_item}
		if not item_names:
			return

		requirement_items = {
			row.name: row
			for row in frappe.get_all(
				"Delivery Order Item",
				filters={"name": ["in", list(item_names)]},
				fields=["name", "item_code", "item_name", "transport_qty"],
			)
		}
		for item in self.items:
			if not item.delivery_order_item:
				continue
			requirement_item = requirement_items.get(item.delivery_order_item)
			if not requirement_item:
				frappe.throw(_("Delivery Order Item {0} could not be found.").format(item.delivery_order_item))
			item.item_code = requirement_item.item_code
			item.item_name = requirement_item.item_name
			if not flt(item.ordered_qty):
				item.ordered_qty = requirement_item.transport_qty

	def validate_exception_reason(self):
		if self.status in ("Partially Delivered", "Rejected", "Failed") and not self.exception_reason:
			frappe.throw(_("An exception reason is required for {0} deliveries.").format(self.status.lower()))

	def on_submit(self):
		sync_delivery_exception_from_pod(self)
		update_delivery_stop_from_pod(self, self.status)

	def on_cancel(self):
		cancel_delivery_exception_from_pod(self)
		reset_delivery_stop_from_pod(self)
