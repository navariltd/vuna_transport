# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class TransportSettings(Document):
	def validate(self):
		if self.load_suggestion_option_count < 3 or self.load_suggestion_option_count > 10:
			frappe.throw(_("Load Suggestion Options must be between 3 and 10."))
		from vuna_transport.services.demand.source_policy_service import SUPPORTED_SOURCES

		if not self.get("demand_sources"):
			frappe.throw(
				_(
					"Configure at least one Transport Demand Source. Disable its row if generation is not required."
				)
			)
		seen = set()
		for row in self.demand_sources:
			if row.source_document_type not in SUPPORTED_SOURCES or row.source_document_type in seen:
				frappe.throw(_("Transport Demand Sources must be supported and unique."))
			seen.add(row.source_document_type)
			if row.creation_mode not in ("Manual", "Automatic"):
				frappe.throw(_("Choose Manual or Automatic demand creation."))
			if row.required_workflow_state and not frappe.get_meta(row.source_document_type).has_field(
				"workflow_state"
			):
				frappe.throw(
					_("Configure a workflow on {0} before selecting a required workflow state.").format(
						row.source_document_type
					)
				)
			if row.source_document_type != "Sales Order" and row.auto_submit_delivery_orders:
				frappe.throw(_("Only Sales Orders generate Delivery Orders."))

	def on_update(self):
		frappe.clear_cache(doctype="Transport Settings")
