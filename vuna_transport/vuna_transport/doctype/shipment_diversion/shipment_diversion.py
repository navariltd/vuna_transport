import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from vuna_transport.services.execution.shipment_diversion_documents import guard_diversion_cancellation
from vuna_transport.services.execution.shipment_diversion_service import validate_shipment_diversion


class ShipmentDiversion(Document):
	def validate(self):
		validate_shipment_diversion(self)
		self.status = "Approved" if self.docstatus == 1 else "Draft"
		if self.docstatus == 0:
			self.approved_by = self.approved_on = None

	def before_submit(self):
		self.approved_by = frappe.session.user
		self.approved_on = now_datetime()

	def on_cancel(self):
		self.db_set("status", "Cancelled", update_modified=False)

	def before_cancel(self):
		guard_diversion_cancellation(self)
