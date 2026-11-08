import frappe
from frappe.model.document import Document


class TransportTripTask(Document):
	def validate(self):
		if not self.flags.from_trip_planner:
			frappe.throw(frappe._("Trip tasks are maintained by their Delivery Trip and execution proof."))
