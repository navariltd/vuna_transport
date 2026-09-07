# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class TransportSettings(Document):
	def validate(self):
		if self.load_suggestion_option_count < 3 or self.load_suggestion_option_count > 10:
			frappe.throw(_("Load Suggestion Options must be between 3 and 10."))
