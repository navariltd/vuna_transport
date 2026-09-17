# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from vuna_transport.services.execution.delivery_exception_service import (
	recompute_exception_context,
	validate_exception_cancellation,
	validate_exception_submission,
	validate_delivery_exception,
	validate_exception_deletion,
)


class DeliveryException(Document):
	def validate(self):
		validate_delivery_exception(self)

	def on_update(self):
		recompute_exception_context(self)

	def before_submit(self):
		validate_exception_submission(self)

	def before_cancel(self):
		validate_exception_cancellation(self)

	def on_cancel(self):
		recompute_exception_context(self)

	def on_trash(self):
		validate_exception_deletion(self)

	def after_delete(self):
		recompute_exception_context(self)
