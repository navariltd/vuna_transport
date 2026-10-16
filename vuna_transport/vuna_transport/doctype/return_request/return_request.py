# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from vuna_transport.services.execution.return_service import validate_return_request


class ReturnRequest(Document):
	def before_insert(self):
		if not self.activities:
			self.append_activity("Created", _("Return Request created."))
		if self.custody:
			self.append_custody_event(
				None, self.custody, self.responsible_party, _("Initial custody recorded.")
			)

	def validate(self):
		validate_return_request(self)

	def before_submit(self):
		if self.status != "Requested":
			frappe.throw(_("A Return Request must be submitted from Requested status."))

	def append_activity(self, action, remarks=None, reference=None):
		"""Keep a concise operational timeline without duplicating ERPNext ledgers."""
		self.append(
			"activities",
			{
				"status": self.status,
				"action": action,
				"activity_on": now_datetime(),
				"user": frappe.session.user,
				"remarks": remarks,
				"reference_doctype": getattr(reference, "doctype", None),
				"reference_name": getattr(reference, "name", None),
			},
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
