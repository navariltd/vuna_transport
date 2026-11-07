# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class DeliveryMethod(Document):
	def validate(self):
		from vuna_transport.services.demand.delivery_method_service import validate_method_configuration

		validate_method_configuration(self)
