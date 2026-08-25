# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

from frappe.model.document import Document
from frappe.utils import now_datetime

from vuna_transport.services.fleet.vehicle_allocation_service import (
	activate_allocation,
	restore_previous_allocation,
	validate_allocation,
	validate_allocation_cancellation,
)


class VehicleAllocation(Document):
	def validate(self):
		validate_allocation(self)

	def before_submit(self):
		self.status = "Allocated"
		self.effective_from = self.effective_from or now_datetime()

	def on_submit(self):
		activate_allocation(self)

	def before_cancel(self):
		validate_allocation_cancellation(self)

	def on_cancel(self):
		self.db_set("status", "Cancelled", update_modified=False)
		restore_previous_allocation(self)
