# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from vuna_transport.services.execution.return_service import (
	recalculate_return_request,
	validate_return_request,
)


class ReturnRequest(Document):
	def before_insert(self):
		if not self.process_version:
			self.process_version = "Custody and Inspection"
		if self.custody:
			self.append_custody_event(
				None, self.custody, self.responsible_party, _("Initial custody recorded.")
			)

	def validate(self):
		validate_return_request(self)

	def before_update_after_submit(self):
		from vuna_transport.services.demand.delivery_method_service import validate_source_delivery_method

		validate_source_delivery_method(self)

	def before_submit(self):
		for row in self.items:
			if not row.return_reason:
				frappe.throw(_("Item Return Reason is required for {0}.").format(row.item_code))

	def on_submit(self):
		recalculate_return_request(self.name)
		if self.return_context == "Depot":
			from vuna_transport.services.demand.source_policy_service import automatically_create_from_source

			automatically_create_from_source(self)

	def before_cancel(self):
		from vuna_transport.services.execution.direct_return_pickup_service import guard_return_cancellation

		guard_return_cancellation(self)
		if self.return_context == "Depot":
			if frappe.db.exists("Return Inspection", {"return_request": self.name, "docstatus": ["!=", 2]}):
				frappe.throw(_("Cancel or delete depot Return Inspections before cancelling this request."))
			from vuna_transport.services.execution.depot_return_service import depot_return_state

			if depot_return_state(self)["returned_qty"]:
				frappe.throw(_("Cancel the receiving Stock Entries before cancelling this depot return."))
			from vuna_transport.services.planning.demand_lifecycle_service import guard_source_cancellation

			guard_source_cancellation(self)
		self.ignore_linked_doctypes = ["Proof of Delivery", "Transport Trip Task"]
		if self.process_version == "Custody and Inspection":
			self.ignore_linked_doctypes.append("Return Inspection")
		for doctype, fieldname, label in (
			("Delivery Note", "return_stock_document", _("Sales Return")),
			("Sales Invoice", "credit_note", _("Credit Note")),
			("Sales Order", "replacement_sales_order", _("Replacement Sales Order")),
			("Payment Entry", "refund_payment_entry", _("Refund Payment Entry")),
		):
			name = self.get(fieldname)
			if not name:
				continue
			docstatus = frappe.db.get_value(doctype, name, "docstatus")
			if docstatus == 0:
				frappe.throw(
					_("Delete draft {0} {1} before cancelling this Return Request.").format(label, name)
				)
			if docstatus == 1:
				frappe.throw(_("Cancel {0} {1} before cancelling this Return Request.").format(label, name))

	def on_cancel(self):
		from vuna_transport.services.execution.unplanned_pickup_service import unlink_collection_cases

		unlink_collection_cases(self)
		self.unlink_proof_of_delivery()
		self.db_set("status", "Cancelled", update_modified=False)

	def on_trash(self):
		from vuna_transport.services.execution.unplanned_pickup_service import unlink_collection_cases

		unlink_collection_cases(self)
		self.unlink_proof_of_delivery()

	def unlink_proof_of_delivery(self):
		"""Clear the convenience backlink without weakening return lineage checks."""
		if not self.proof_of_delivery:
			return
		linked_request = frappe.db.get_value("Proof of Delivery", self.proof_of_delivery, "return_request")
		if linked_request == self.name:
			frappe.db.set_value(
				"Proof of Delivery",
				self.proof_of_delivery,
				"return_request",
				None,
				update_modified=False,
			)

	def append_custody_event(self, from_custody, to_custody, responsible_party=None, remarks=None):
		self.append(
			"custody_events",
			{
				"event_on": now_datetime(),
				"from_custody": from_custody,
				"to_custody": to_custody,
				"responsible_party": responsible_party,
				"user": frappe.session.user,
				"remarks": remarks,
				"reference_doctype": self.doctype,
				"reference_name": self.name,
			},
		)
