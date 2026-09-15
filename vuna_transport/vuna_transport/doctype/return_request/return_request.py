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

	def validate(self):
		validate_return_request(self)

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
