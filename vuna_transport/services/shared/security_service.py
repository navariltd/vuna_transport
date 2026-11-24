"""Permission and company-access checks shared by transport APIs."""

import frappe
from frappe import _


def require_authenticated():
	if not frappe.session.user or frappe.session.user == "Guest":
		frappe.throw(_("Authentication is required."), frappe.PermissionError)


def require_doctype_permission(doctype, permission_type):
	require_authenticated()
	if not frappe.has_permission(doctype, permission_type):
		frappe.throw(
			_("You do not have {0} permission for {1}.").format(permission_type, doctype),
			frappe.PermissionError,
		)


def require_document_permission(doc, permission_type="read"):
	require_authenticated()
	doc.check_permission(permission_type)
	company = getattr(doc, "company", None)
	if company:
		require_company_access(company)


def require_company_access(company):
	if not company or frappe.session.user == "Administrator":
		return
	allowed = frappe.get_list(
		"Company",
		filters={"name": company},
		pluck="name",
		limit_page_length=1,
	)
	if company not in allowed:
		frappe.throw(
			_("You do not have access to Company {0}.").format(company),
			frappe.PermissionError,
		)


def filter_readable_rows(doctype, rows):
	"""Keep aggregate report rows within the user's document and company scope."""
	require_doctype_permission(doctype, "read")
	if not rows:
		return []
	names = list({row["name"] for row in rows})
	readable = set(frappe.get_list(doctype, filters={"name": ["in", names]}, pluck="name", limit=len(names)))
	return [row for row in rows if row["name"] in readable]
