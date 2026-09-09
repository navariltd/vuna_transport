# Copyright (c) 2026, Yovuna Technologies Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate, nowdate

MIN_MODEL_YEAR = 1886


class VehicleModel(Document):
	def validate(self):
		validate_model_year(self.model_year)
		settings = frappe.get_cached_doc("Transport Settings")
		if not self.weight_uom:
			self.weight_uom = settings.default_weight_uom
		if not self.volume_uom:
			self.volume_uom = settings.default_volume_uom


def validate_model_year(model_year):
	"""Allow an unknown year, but reject zero, implausible, or future years."""
	if model_year in (None, ""):
		return
	year = cint(model_year)
	maximum_year = getdate(nowdate()).year + 1
	if year < MIN_MODEL_YEAR or year > maximum_year:
		frappe.throw(
			_("Model Year must be between {0} and {1}, or left blank if unknown.").format(
				MIN_MODEL_YEAR, maximum_year
			),
		)
