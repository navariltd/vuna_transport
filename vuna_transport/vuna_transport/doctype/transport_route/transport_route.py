# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from vuna_transport.services.route_service import validate_transport_route


class TransportRoute(Document):
	def validate(self):
		validate_transport_route(self)
