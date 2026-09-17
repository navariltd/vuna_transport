# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class Town(Document):
	def validate(self):
		if self.constituency:
			constituency = frappe.db.get_value(
				"Constituency", self.constituency, ["county", "disabled"], as_dict=True
			)
			if not constituency:
				frappe.throw(_("Constituency {0} does not exist.").format(self.constituency))
			if constituency.disabled:
				frappe.throw(_("Town cannot belong to a disabled Constituency."))
			if not self.county:
				self.county = constituency.county
			elif self.county != constituency.county:
				frappe.throw(_("Constituency {0} does not belong to County {1}.").format(self.constituency, self.county))
