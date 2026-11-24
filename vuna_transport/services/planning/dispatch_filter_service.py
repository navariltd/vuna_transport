"""Safe, company-specific configuration for additional Dispatch Planning filters."""

import frappe
from frappe import _

from vuna_transport.services.planning import customer_planning_source as source
from vuna_transport.vuna_transport.doctype.dispatch_planning_filter.dispatch_planning_filter import (
	ALLOWED_FILTER_KEYS,
)


def _filter_definitions():
	"""Translate labels in the requesting site's language, not at module import."""
	return {
		"delivery_group": {"label": _("Delivery Group"), "fieldtype": "Link", "options": "Delivery Group"},
		"transport_compatibility_group": {
			"label": _("Transport Compatibility Group"),
			"fieldtype": "Link",
			"options": "Transport Compatibility Group",
		},
		"preferred_route": {"label": _("Preferred Route"), "fieldtype": "Link", "options": "Transport Route"},
		"maximum_vehicle_category": {
			"label": _("Maximum Vehicle Category"),
			"fieldtype": "Link",
			"options": "Vehicle Category",
		},
		"delivery_address": {"label": _("Delivery Address"), "fieldtype": "Link", "options": "Address"},
		"territory": {"label": _("Territory"), "fieldtype": "Link", "options": "Territory"},
		"county": {"label": _("County"), "fieldtype": "Link", "options": "County"},
		"constituency": {
			"label": _("Constituency / Sub-county"),
			"fieldtype": "Link",
			"options": "Constituency",
		},
		"town": {"label": _("Town"), "fieldtype": "Link", "options": "Town"},
		"transport_zone": {"label": _("Transport Zone"), "fieldtype": "Link", "options": "Transport Zone"},
		"delivery_method": {"label": _("Delivery Method"), "fieldtype": "Link", "options": "Delivery Method"},
		"priority": {"label": _("Priority"), "fieldtype": "Select", "options": "Low\nMedium\nHigh\nUrgent"},
		"customer": {"label": _("Customer"), "fieldtype": "Link", "options": "Customer"},
		"source_warehouse": {"label": _("Source Warehouse"), "fieldtype": "Link", "options": "Warehouse"},
		"transport_handling_profile": {"label": _("Transport Handling Profile"), "fieldtype": "Data"},
	}


ALLOWED_FILTER_OPERATORS = {
	"=",
	"!=",
	"like",
	"not like",
	"in",
	"not in",
	">",
	"<",
	">=",
	"<=",
	"between",
	"is",
}
PROTECTED_FILTER_FIELDS = {"company", "docstatus", "status"}


def get_filter_definitions(company=None):
	"""Return active global additional filter definitions."""
	rows = frappe.get_list(
		"Dispatch Planning Filter",
		filters={"disabled": 0},
		fields=["name", "field_key", "filter_label", "sequence"],
		order_by="sequence asc, filter_label asc",
		limit_page_length=50,
	)
	definitions = []
	filter_definitions = _filter_definitions()
	for row in rows:
		definition = filter_definitions.get(row.field_key)
		if not definition:
			continue
		definitions.append(
			{
				"name": row.name,
				"field_key": row.field_key,
				"label": row.filter_label or definition["label"],
				"fieldtype": definition["fieldtype"],
				"options": definition.get("options"),
				"sequence": row.sequence or 0,
			}
		)
	return sorted(
		definitions,
		key=lambda row: (row["sequence"], row["label"]),
	)


def validate_configured_filters(company, planning_filters):
	"""Return only valid configured filters for a company."""
	if not planning_filters:
		return {}
	if isinstance(planning_filters, str):
		planning_filters = frappe.parse_json(planning_filters)
	if not isinstance(planning_filters, dict):
		frappe.throw(_("Planning filters must be an object."))
	configured = {row["field_key"] for row in get_filter_definitions(company)}
	unknown = set(planning_filters) - configured
	if unknown:
		frappe.throw(
			_("These Dispatch Planning filters are not configured: {0}.").format(", ".join(sorted(unknown)))
		)
	return {key: value for key, value in planning_filters.items() if value not in (None, "")}


def validate_additional_filters(additional_filters):
	"""Validate native FilterGroup expressions against Delivery Order metadata."""
	if not additional_filters:
		return []
	if isinstance(additional_filters, str):
		additional_filters = frappe.parse_json(additional_filters)
	if not isinstance(additional_filters, list) or len(additional_filters) > 25:
		frappe.throw(_("Add no more than 25 Transport Demand filters."))
	target = source.doctype()
	meta = frappe.get_meta(target)
	conditions = []
	for item in additional_filters:
		if not isinstance(item, (list, tuple)) or len(item) < 4:
			frappe.throw(_("Invalid Dispatch Planning filter format."))
		doctype, fieldname, operator, value = item[:4]
		if doctype != target:
			frappe.throw(_("Dispatch Planning filters must target {0}.").format(target))
		if fieldname in PROTECTED_FILTER_FIELDS | {
			"source_document_type",
			"source_document",
			"movement_type",
		}:
			frappe.throw(
				_("The {0} filter is controlled by Dispatch Planning security rules.").format(fieldname)
			)
		if fieldname not in ("name", "creation", "modified", "owner", "modified_by") and not meta.get_field(
			fieldname
		):
			frappe.throw(_("{0} field {1} does not exist or is not permitted.").format(target, fieldname))
		if operator not in ALLOWED_FILTER_OPERATORS:
			frappe.throw(_("Filter operator {0} is not supported.").format(operator))
		conditions.append([target, fieldname, operator, value])
	return conditions


def as_filter_conditions(filters, additional_filters=None):
	"""Convert standard Frappe filter mappings and FilterGroup rows to one safe list."""
	conditions = []
	for fieldname, value in source.filters(filters or {}).items():
		if isinstance(value, (list, tuple)) and len(value) == 2:
			conditions.append([source.doctype(), fieldname, value[0], value[1]])
		else:
			conditions.append([source.doctype(), fieldname, "=", value])
	return conditions + list(additional_filters or [])
