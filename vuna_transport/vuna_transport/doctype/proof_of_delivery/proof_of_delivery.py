# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from vuna_transport.services.execution.pod_service import (
	derive_pod_status,
	reset_delivery_stop_from_pod,
	update_delivery_stop_from_pod,
	validate_pod_items,
)
from vuna_transport.services.execution.return_service import create_return_request_from_pod
from vuna_transport.services.execution.stock_identity_service import (
	get_single_delivery_note_item_batch,
)


class ProofofDelivery(Document):
	def validate(self):
		if self.get("proof_type") == "Unplanned Collection":
			from vuna_transport.services.execution.unplanned_pickup_service import validate_collection

			validate_collection(self)
			return
		if self.get("transport_trip_task"):
			if self.proof_type == "Return Pickup":
				from vuna_transport.services.execution.return_pickup_service import validate_pickup_proof

				validate_pickup_proof(self)
				return
			from vuna_transport.services.execution.transfer_proof_service import validate_proof

			validate_proof(self)
			return
		if self.get("proof_type") not in (None, "", "Customer Delivery"):
			frappe.throw(_("Transfer and pickup PODs require an authoritative transport task."))
		self.proof_type, self.direction = "Customer Delivery", "Delivery"
		self.populate_stop_details()
		self.validate_unique_active_pod()
		self.populate_item_details()
		validate_pod_items(self.items, self.rejection_reasons)
		self.total_accepted_qty = sum(flt(item.delivered_qty) for item in self.items)
		self.total_rejected_qty = sum(flt(item.rejected_qty) for item in self.items)
		self.status = derive_pod_status(self.items)

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

	def validate_unique_active_pod(self):
		duplicate = frappe.db.get_value(
			"Proof of Delivery",
			{
				"delivery_trip": self.delivery_trip,
				"delivery_stop": self.delivery_stop,
				"transport_trip_task": ["is", "not set"],
				"docstatus": ["!=", 2],
				"name": ["!=", self.name or ""],
			},
			"name",
		)
		if duplicate:
			frappe.throw(
				_("Delivery Stop {0} already has active Proof of Delivery {1}.").format(
					self.delivery_stop, duplicate
				)
			)

	def populate_item_details(self):
		"""Populate item snapshots with one batched query instead of one query per row."""
		item_names = {item.delivery_order_item for item in self.items if item.delivery_order_item}
		requirement_items = {}
		if item_names:
			requirement_items = {
				row.name: row
				for row in frappe.get_all(
					"Delivery Order Item",
					filters={"name": ["in", list(item_names)]},
					fields=["name"],
				)
			}
		delivery_note_item_names = {item.delivery_note_item for item in self.items if item.delivery_note_item}
		delivery_note_items = {}
		if delivery_note_item_names:
			delivery_note_items = {
				row.name: row
				for row in frappe.get_all(
					"Delivery Note Item",
					filters={"parent": self.delivery_note, "name": ["in", list(delivery_note_item_names)]},
					fields=[
						"name",
						"item_code",
						"item_name",
						"qty",
						"uom",
						"stock_uom",
						"conversion_factor",
						"batch_no",
						"serial_and_batch_bundle",
					],
				)
			}
		for item in self.items:
			delivery_note_item = delivery_note_items.get(item.delivery_note_item)
			if not delivery_note_item:
				frappe.throw(
					_("Delivery Note Item {0} could not be found in Delivery Note {1}.").format(
						item.delivery_note_item, self.delivery_note
					)
				)
			item.item_code = delivery_note_item.item_code
			item.item_name = delivery_note_item.item_name
			item.uom = delivery_note_item.uom
			item.stock_uom = delivery_note_item.stock_uom
			item.conversion_factor = flt(delivery_note_item.conversion_factor) or 1
			item.batch_no = item.batch_no or get_single_delivery_note_item_batch(delivery_note_item)
			if not flt(item.ordered_qty):
				item.ordered_qty = flt(delivery_note_item.qty)
			if not item.delivery_order_item:
				continue
			requirement_item = requirement_items.get(item.delivery_order_item)
			if not requirement_item:
				frappe.throw(
					_("Delivery Order Item {0} could not be found.").format(item.delivery_order_item)
				)

	def on_submit(self):
		if self.proof_type == "Unplanned Collection":
			return
		if self.get("transport_trip_task"):
			if self.proof_type == "Return Pickup":
				from vuna_transport.services.execution.return_pickup_service import (
					post_pickup_stock,
					sync_pickup_proof,
				)

				post_pickup_stock(self)
				sync_pickup_proof(self)
				return
			from vuna_transport.services.execution.depot_return_service import create_depot_return_from_pod
			from vuna_transport.services.execution.transfer_proof_service import submit_receipt_stock_from_pod

			submit_receipt_stock_from_pod(self)
			create_depot_return_from_pod(self)
			return
		update_delivery_stop_from_pod(self, self.status)
		if self.status in ("Partially Delivered", "Rejected", "Failed"):
			create_return_request_from_pod(self.name)

	@frappe.whitelist()
	def create_return_request(self):
		self.check_permission("read")
		if self.docstatus != 1:
			frappe.throw(_("Submit the Proof of Delivery before creating a Return Request."))
		if self.get("transport_trip_task"):
			from vuna_transport.services.execution.depot_return_service import create_depot_return_from_pod

			return create_depot_return_from_pod(self)
		if self.status not in ("Partially Delivered", "Rejected", "Failed"):
			frappe.throw(_("Accepted Proofs of Delivery do not require a Return Request."))
		return create_return_request_from_pod(self.name)

	def before_cancel(self):
		if self.proof_type == "Unplanned Collection":
			if self.return_request and frappe.db.exists(
				"Return Request",
				{"name": self.return_request, "proof_of_delivery": self.name, "docstatus": ["!=", 2]},
			):
				frappe.throw(_("Resolve the linked Return Request before cancelling collection evidence."))
			return
		from vuna_transport.services.execution.pod_service import prepare_pod_cancellation

		prepare_pod_cancellation(self)
		if self.get("transport_trip_task"):
			if self.proof_type == "Return Pickup":
				from vuna_transport.services.execution.return_pickup_service import (
					guard_pickup_proof_cancellation,
				)

				guard_pickup_proof_cancellation(self)
				return
			from vuna_transport.services.execution.transfer_proof_service import guard_proof_cancellation

			guard_proof_cancellation(self)

	def on_trash(self):
		if self.proof_type == "Unplanned Collection":
			return
		from vuna_transport.services.execution.pod_service import clear_pod_stop_reference

		if not self.get("transport_trip_task"):
			clear_pod_stop_reference(self)

	def on_cancel(self):
		if self.proof_type == "Unplanned Collection":
			return
		if self.proof_type == "Return Pickup":
			from vuna_transport.services.execution.return_pickup_service import sync_pickup_proof

			sync_pickup_proof(self)
		if not self.get("transport_trip_task"):
			reset_delivery_stop_from_pod(self)
