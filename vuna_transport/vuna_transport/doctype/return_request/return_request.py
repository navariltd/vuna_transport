# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from vuna_transport.services.execution.return_service import validate_return_request


class ReturnRequest(Document):
	def validate(self):
		validate_return_request(self)
