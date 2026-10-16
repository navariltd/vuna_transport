"""Permission-checked ERPNext and HRMS requests raised by assigned mobile drivers."""

import html
import re

import frappe
from frappe import _
from frappe.utils import cint, escape_html, flt, getdate, nowdate, strip_html_tags

from vuna_transport.services.mobile.mobile_device_service import (
	_get_assigned_trip,
	_require_mobile_driver,
)
from vuna_transport.services.shared.security_service import require_doctype_permission

MATERIAL_REQUEST_PURPOSES = {"Purchase", "Material Issue"}
MAX_MATERIAL_REQUEST_ITEMS = 20
MAX_EXPENSE_CLAIM_ITEMS = 20
REQUEST_ID_PATTERN = re.compile(r"^[a-f0-9-]{16,64}$", re.IGNORECASE)
MAX_REQUEST_HISTORY_ROWS = 100


def get_mobile_requests(installation_id, device_identifier=None, limit=50):
	"""Return recent mobile-originated requests owned by the current driver user."""
	driver = _require_mobile_driver(installation_id, device_identifier)
	limit = min(max(cint(limit) or 50, 1), MAX_REQUEST_HISTORY_ROWS)
	requests = []

	if driver.employee and frappe.has_permission("Expense Claim", "read"):
		expense_fields = [
			"name",
			"status",
			"approval_status",
			"posting_date",
			"modified",
			"grand_total",
			"total_claimed_amount",
			"currency",
			"delivery_trip",
		]
		if frappe.get_meta("Expense Claim").has_field("workflow_state"):
			expense_fields.append("workflow_state")
		for row in frappe.get_list(
			"Expense Claim",
			filters={
				"employee": driver.employee,
				"remark": ["like", "%Mobile Request ID:%"],
			},
			fields=expense_fields,
			order_by="modified desc",
			limit_page_length=limit,
		):
			requests.append(
				{
					"doctype": "Expense Claim",
					"name": row.name,
					"status": row.get("workflow_state")
					or (
						row.approval_status if row.approval_status not in (None, "", "Draft") else row.status
					),
					"document_status": row.status,
					"date": row.posting_date,
					"modified": row.modified,
					"delivery_trip": row.delivery_trip,
					"amount": row.grand_total or row.total_claimed_amount,
					"currency": row.currency,
				}
			)

	if frappe.has_permission("Material Request", "read"):
		material_fields = [
			"name",
			"status",
			"transaction_date",
			"modified",
			"material_request_type",
			"terms",
		]
		if frappe.get_meta("Material Request").has_field("workflow_state"):
			material_fields.append("workflow_state")
		material_requests = frappe.get_list(
			"Material Request",
			filters={
				"owner": frappe.session.user,
				"terms": ["like", "%Mobile Request ID:%"],
			},
			fields=material_fields,
			order_by="modified desc",
			limit_page_length=limit,
		)
		item_counts = _get_material_request_item_counts([row.name for row in material_requests])
		for row in material_requests:
			requests.append(
				{
					"doctype": "Material Request",
					"name": row.name,
					"status": row.get("workflow_state") or row.status,
					"document_status": row.status,
					"date": row.transaction_date,
					"modified": row.modified,
					"delivery_trip": _extract_request_context(row.terms, "Delivery Trip"),
					"purpose": row.material_request_type,
					"item_count": item_counts.get(row.name, 0),
				}
			)

	requests.sort(key=lambda row: str(row.get("modified") or ""), reverse=True)
	return {"requests": requests[:limit]}


def get_mobile_request_details(
	installation_id,
	doctype,
	name,
	device_identifier=None,
):
	"""Return itemized details for one request belonging to the mobile driver."""
	driver = _require_mobile_driver(installation_id, device_identifier)
	if doctype not in {"Expense Claim", "Material Request"} or not name:
		frappe.throw(_("Select a valid mobile request."))
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")

	if doctype == "Expense Claim":
		if (
			not driver.employee
			or doc.employee != driver.employee
			or "Mobile Request ID:" not in (doc.remark or "")
		):
			frappe.throw(
				_("This Expense Claim does not belong to your mobile requests."), frappe.PermissionError
			)
		status = doc.get("workflow_state") or (
			doc.approval_status if doc.approval_status not in (None, "", "Draft") else doc.status
		)
		return {
			"doctype": doc.doctype,
			"name": doc.name,
			"status": status,
			"document_status": doc.status,
			"date": doc.posting_date,
			"delivery_trip": doc.delivery_trip,
			"amount": doc.grand_total or doc.total_claimed_amount,
			"currency": doc.currency,
			"items": [
				{
					"expense_type": row.expense_type,
					"expense_date": row.expense_date,
					"description": row.description,
					"amount": row.amount,
					"sanctioned_amount": row.sanctioned_amount,
				}
				for row in doc.expenses
			],
		}

	if doc.owner != frappe.session.user or "Mobile Request ID:" not in (doc.terms or ""):
		frappe.throw(
			_("This Material Request does not belong to your mobile requests."), frappe.PermissionError
		)
	return {
		"doctype": doc.doctype,
		"name": doc.name,
		"status": doc.get("workflow_state") or doc.status,
		"document_status": doc.status,
		"date": doc.transaction_date,
		"delivery_trip": _extract_request_context(doc.terms, "Delivery Trip"),
		"purpose": doc.material_request_type,
		"items": [
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"qty": row.qty,
				"uom": row.uom,
				"warehouse": row.warehouse,
				"schedule_date": row.schedule_date,
			}
			for row in doc.items
		],
	}


def get_mobile_request_setup(installation_id, trip_name, device_identifier=None):
	"""Return bounded configuration needed by the mobile request forms."""
	driver, trip = _get_mobile_request_context(installation_id, trip_name, device_identifier)
	employee = None
	if driver.employee:
		employee = frappe.db.get_value(
			"Employee",
			driver.employee,
			["name", "employee_name", "company", "expense_approver"],
			as_dict=True,
		)

	expense_types = []
	if frappe.has_permission("Expense Claim Type", "read"):
		configured_types = frappe.get_all(
			"Expense Claim Account",
			filters={"company": trip.company},
			pluck="parent",
			limit_page_length=200,
		)
		if configured_types:
			expense_types = frappe.get_list(
				"Expense Claim Type",
				filters={"name": ["in", list(dict.fromkeys(configured_types))]},
				pluck="name",
				order_by="name asc",
				limit_page_length=100,
			)

	warehouses = []
	if frappe.has_permission("Warehouse", "read"):
		warehouses = frappe.get_list(
			"Warehouse",
			filters={"company": trip.company, "is_group": 0, "disabled": 0},
			fields=["name", "warehouse_name"],
			order_by="warehouse_name asc",
			limit_page_length=100,
		)

	return {
		"trip": {"name": trip.name, "company": trip.company},
		"driver": {"name": driver.name, "employee": driver.employee},
		"employee": employee,
		"expense_types": expense_types,
		"warehouses": warehouses,
		"permissions": {
			"expense_claim": bool(driver.employee and frappe.has_permission("Expense Claim", "create")),
			"material_request": bool(frappe.has_permission("Material Request", "create")),
		},
	}


def search_mobile_material_request_items(
	installation_id,
	trip_name,
	txt=None,
	device_identifier=None,
):
	"""Search a small set of enabled stock Items for an assigned driver's request."""
	_get_mobile_request_context(installation_id, trip_name, device_identifier)
	require_doctype_permission("Material Request", "create")
	if not frappe.has_permission("Item", "read"):
		frappe.throw(_("You do not have permission to view Items."), frappe.PermissionError)

	search_text = (txt or "").strip()
	filters = {"disabled": 0, "is_stock_item": 1, "has_variants": 0}
	or_filters = None
	if search_text:
		like = f"%{search_text}%"
		or_filters = {"name": ["like", like], "item_name": ["like", like]}

	return frappe.get_list(
		"Item",
		filters=filters,
		or_filters=or_filters,
		fields=["name", "item_name", "stock_uom"],
		order_by="modified desc",
		limit_page_length=20,
	)


def create_mobile_expense_claim(
	installation_id,
	trip_name,
	payload,
	device_identifier=None,
):
	"""Create one HRMS Expense Claim draft linked to the assigned Delivery Trip."""
	driver, trip = _get_mobile_request_context(installation_id, trip_name, device_identifier)
	require_doctype_permission("Expense Claim", "create")
	if not driver.employee:
		frappe.throw(_("Your Driver profile must be linked to an Employee before claiming expenses."))
	payload = _parse_payload(payload)
	request_id = _validate_request_id(payload.get("request_id"))
	existing = _find_existing_request("Expense Claim", "remark", request_id)
	if existing:
		return {"doctype": "Expense Claim", "name": existing, "status": "Draft", "created": False}

	expenses = payload.get("expenses")
	if expenses is None:
		# Retain compatibility with mobile builds that sent one expense directly.
		expenses = [
			{
				"expense_type": payload.get("expense_type"),
				"amount": payload.get("amount"),
				"expense_date": payload.get("expense_date"),
				"description": payload.get("description"),
			}
		]
	if not isinstance(expenses, list) or not expenses or len(expenses) > MAX_EXPENSE_CLAIM_ITEMS:
		frappe.throw(_("Add between 1 and {0} expense rows.").format(MAX_EXPENSE_CLAIM_ITEMS))

	expense_types = {
		row.get("expense_type") for row in expenses if isinstance(row, dict) and row.get("expense_type")
	}
	configured_types = (
		set(
			frappe.get_all(
				"Expense Claim Account",
				filters={"parent": ["in", list(expense_types)], "company": trip.company},
				pluck="parent",
			)
		)
		if expense_types
		else set()
	)
	validated_expenses = []
	for index, row in enumerate(expenses, 1):
		if not isinstance(row, dict):
			frappe.throw(_("Expense row {0} is invalid.").format(index))
		expense_type = row.get("expense_type")
		amount = flt(row.get("amount"))
		if not expense_type or expense_type not in configured_types:
			frappe.throw(
				_("Expense row {0}: select a type configured for Company {1}.").format(index, trip.company)
			)
		if amount <= 0:
			frappe.throw(_("Expense row {0}: amount must be greater than zero.").format(index))
		validated_expenses.append(
			{
				"expense_date": getdate(row.get("expense_date") or nowdate()),
				"expense_type": expense_type,
				"description": row.get("description"),
				"amount": amount,
			}
		)

	claim = frappe.new_doc("Expense Claim")
	claim.employee = driver.employee
	claim.company = trip.company
	claim.delivery_trip = trip.name
	claim.expense_approver = frappe.db.get_value("Employee", driver.employee, "expense_approver")
	claim.remark = _request_context(request_id, trip.name, driver.name, payload.get("remarks"))
	for expense in validated_expenses:
		claim.append("expenses", expense)
	claim.insert()
	return {"doctype": claim.doctype, "name": claim.name, "status": claim.status, "created": True}


def create_mobile_material_request(
	installation_id,
	trip_name,
	payload,
	device_identifier=None,
):
	"""Create one ERPNext Material Request draft for review and normal processing."""
	driver, trip = _get_mobile_request_context(installation_id, trip_name, device_identifier)
	require_doctype_permission("Material Request", "create")
	payload = _parse_payload(payload)
	request_id = _validate_request_id(payload.get("request_id"))
	existing = _find_existing_request("Material Request", "terms", request_id)
	if existing:
		return {"doctype": "Material Request", "name": existing, "status": "Draft", "created": False}

	purpose = payload.get("purpose") or "Purchase"
	if purpose not in MATERIAL_REQUEST_PURPOSES:
		frappe.throw(_("Mobile Material Requests support Purchase or Material Issue only."))
	schedule_date = getdate(payload.get("schedule_date") or nowdate())
	if schedule_date < getdate(nowdate()):
		frappe.throw(_("Required By cannot be before today."))
	warehouse = payload.get("warehouse")
	if purpose == "Material Issue" and not warehouse:
		frappe.throw(_("Select the source Warehouse for a Material Issue request."))
	if warehouse:
		warehouse_company = frappe.db.get_value("Warehouse", warehouse, "company")
		if warehouse_company != trip.company:
			frappe.throw(_("Warehouse must belong to Company {0}.").format(trip.company))

	items = payload.get("items") or []
	if not isinstance(items, list) or not items or len(items) > MAX_MATERIAL_REQUEST_ITEMS:
		frappe.throw(_("Add between 1 and {0} Material Request items.").format(MAX_MATERIAL_REQUEST_ITEMS))
	item_names = [row.get("item_code") for row in items if isinstance(row, dict) and row.get("item_code")]
	item_details = {
		row.name: row
		for row in frappe.get_all(
			"Item",
			filters={
				"name": ["in", list(dict.fromkeys(item_names))],
				"disabled": 0,
				"is_stock_item": 1,
				"has_variants": 0,
			},
			fields=["name", "stock_uom"],
		)
	}
	if len(item_details) != len(set(item_names)):
		frappe.throw(_("One or more selected Items are disabled or unavailable for stock requests."))

	request = frappe.new_doc("Material Request")
	request.company = trip.company
	request.material_request_type = purpose
	request.schedule_date = schedule_date
	request.set_warehouse = warehouse
	request.terms = _request_context_html(request_id, trip.name, driver.name, payload.get("remarks"))
	for row in items:
		item = item_details.get(row.get("item_code"))
		quantity = flt(row.get("qty"))
		if not item or quantity <= 0:
			frappe.throw(_("Every requested Item must have a quantity greater than zero."))
		request.append(
			"items",
			{
				"item_code": item.name,
				"qty": quantity,
				"uom": item.stock_uom,
				"stock_uom": item.stock_uom,
				"conversion_factor": 1,
				"schedule_date": schedule_date,
				"warehouse": warehouse,
			},
		)
	request.insert()
	return {"doctype": request.doctype, "name": request.name, "status": request.status, "created": True}


def _get_mobile_request_context(installation_id, trip_name, device_identifier=None):
	driver = _require_mobile_driver(installation_id, device_identifier)
	trip = _get_assigned_trip(trip_name, driver.name)
	return driver, trip


def _parse_payload(payload):
	return frappe.parse_json(payload) if isinstance(payload, str) else (payload or {})


def _validate_request_id(request_id):
	request_id = (request_id or "").strip()
	if not REQUEST_ID_PATTERN.fullmatch(request_id):
		frappe.throw(_("A valid mobile request ID is required."))
	return request_id


def _find_existing_request(doctype, fieldname, request_id):
	return frappe.db.get_value(
		doctype,
		{
			fieldname: ["like", f"%Mobile Request ID: {request_id}%"],
			"owner": frappe.session.user,
			"docstatus": ["!=", 2],
		},
		"name",
	)


def _request_context(request_id, trip_name, driver_name, remarks=None):
	lines = [
		f"Mobile Request ID: {request_id}",
		f"Delivery Trip: {trip_name}",
		f"Driver: {driver_name}",
	]
	if remarks:
		lines.extend(["", str(remarks).strip()])
	return "\n".join(lines)


def _request_context_html(request_id, trip_name, driver_name, remarks=None):
	context = _request_context(request_id, trip_name, driver_name, remarks)
	return "<br>".join(escape_html(line) for line in context.splitlines())


def _get_material_request_item_counts(request_names):
	if not request_names:
		return {}
	return {
		row.parent: cint(row.item_count)
		for row in frappe.get_all(
			"Material Request Item",
			filters={"parent": ["in", request_names], "parenttype": "Material Request"},
			fields=["parent", {"COUNT": "name", "as": "item_count"}],
			group_by="parent",
		)
	}


def _extract_request_context(value, label):
	if not value:
		return None
	plain_text = re.sub(r"<br\s*/?>", "\n", str(value), flags=re.IGNORECASE)
	plain_text = html.unescape(strip_html_tags(plain_text))
	prefix = f"{label}:"
	for line in plain_text.splitlines():
		if line.strip().startswith(prefix):
			return line.split(":", 1)[1].strip() or None
	return None
