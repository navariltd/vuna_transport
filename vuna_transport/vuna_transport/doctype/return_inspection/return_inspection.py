# Copyright (c) 2026, Yovuna Technologies Limited and contributors

from frappe.model.document import Document

from vuna_transport.services.execution.return_v2_service import validate_return_inspection


class ReturnInspection(Document):
	def validate(self):
		validate_return_inspection(self)

	def on_submit(self):
		self.db_set("status", "Submitted", update_modified=False)
		from vuna_transport.services.execution.return_service import recalculate_return_request

		recalculate_return_request(self.return_request)

	def before_cancel(self):
		from vuna_transport.services.execution.return_v2_service import (
			validate_return_inspection_cancellation,
		)

		validate_return_inspection_cancellation(self)

	def on_cancel(self):
		self.db_set("status", "Cancelled", update_modified=False)
		from vuna_transport.services.execution.return_service import recalculate_return_request

		recalculate_return_request(self.return_request)
