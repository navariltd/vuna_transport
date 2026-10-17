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
		if self.custody:
			self.append_custody_event(
				None, self.custody, self.responsible_party, _("Initial custody recorded.")
			)

	def validate(self):
		validate_return_request(self)

	def before_submit(self):
		for row in self.items:
			if not row.return_reason:
				frappe.throw(_("Item Return Reason is required for {0}.").format(row.item_code))

	def on_submit(self):
		recalculate_return_request(self.name)

	def before_cancel(self):
		self.ignore_linked_doctypes = ["Proof of Delivery"]
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
		self.unlink_proof_of_delivery()
		self.db_set("status", "Cancelled", update_modified=False)

	def on_trash(self):
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
