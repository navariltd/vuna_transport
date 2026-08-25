"""Derived Vehicle compliance using configurable requirements and standard insurance."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, getdate, nowdate


STANDARD_INSURANCE_TYPE = "insurance"


def get_vehicle_compliance(vehicle, at_time=None):
	"""Return derived compliance for one Vehicle without persisting status."""
	vehicle_row = frappe.db.get_value(
		"Vehicle",
		vehicle,
		[
			"name",
			"company",
			"vuna_vehicle_category",
			"vuna_ownership_type",
			"policy_no",
			"start_date",
			"end_date",
		],
		as_dict=True,
	)
	if not vehicle_row:
		return _missing_vehicle_compliance(vehicle)
	return get_vehicle_compliance_map([vehicle_row], at_time).get(vehicle)


def get_vehicle_compliance_map(vehicles, at_time=None):
	"""Evaluate many preloaded Vehicles with bounded compliance queries."""
	vehicles = list(vehicles or [])
	if not vehicles:
		return {}
	evaluation_date = getdate(at_time or nowdate())
	vehicle_names = [vehicle.name for vehicle in vehicles]
	requirements = frappe.get_all(
		"Vehicle Document Type",
		filters={"disabled": 0},
		fields=[
			"name",
			"document_type_name",
			"company",
			"vehicle_category",
			"ownership_type",
			"mandatory_for_dispatch",
			"requires_expiry_date",
			"warning_days_before_expiry",
		],
	)
	documents = frappe.get_all(
		"Vehicle Document",
		filters={"vehicle": ["in", vehicle_names]},
		fields=[
			"name",
			"vehicle",
			"document_type",
			"document_number",
			"issue_date",
			"expiry_date",
			"suspended",
			"cancelled",
		],
		order_by="expiry_date desc, modified desc",
	)
	documents_by_vehicle_and_type = defaultdict(list)
	for document in documents:
		documents_by_vehicle_and_type[(document.vehicle, document.document_type)].append(
			document
		)

	return {
		vehicle.name: _evaluate_vehicle_compliance(
			vehicle,
			requirements,
			documents_by_vehicle_and_type,
			evaluation_date,
		)
		for vehicle in vehicles
	}


def get_missing_mandatory_documents(vehicle, at_time=None):
	return [
		reason
		for reason in get_vehicle_compliance(vehicle, at_time).reasons
		if reason.code in {"DOCUMENT_MISSING", "INSURANCE_MISSING"}
	]


def get_expiring_vehicle_documents(days=30):
	"""Return physical Vehicle Documents expiring within the requested period."""
	today = getdate(nowdate())
	return frappe.get_all(
		"Vehicle Document",
		filters={
			"cancelled": 0,
			"suspended": 0,
			"expiry_date": ["between", [today, add_days(today, int(days))]],
		},
		fields=[
			"name",
			"vehicle",
			"company",
			"document_type",
			"document_number",
			"expiry_date",
		],
		order_by="expiry_date asc",
	)


def _evaluate_vehicle_compliance(
	vehicle, requirements, documents_by_vehicle_and_type, evaluation_date
):
	reasons = []
	warnings = []
	document_results = []
	for requirement in requirements:
		if not _requirement_applies(requirement, vehicle):
			continue
		if _is_standard_insurance(requirement):
			result = _evaluate_standard_insurance(
				vehicle, requirement, evaluation_date
			)
		else:
			candidates = documents_by_vehicle_and_type.get(
				(vehicle.name, requirement.name), []
			)
			result = _select_document_result(
				requirement, candidates, evaluation_date
			)
		document_results.append(result)
		if result.blocking_reason:
			reasons.append(result.blocking_reason)
		if result.warning:
			warnings.append(result.warning)

	return frappe._dict(
		vehicle=vehicle.name,
		compliant=not reasons,
		status="Compliant" if not reasons else "Blocked",
		primary_reason=reasons[0].message if reasons else None,
		reasons=reasons,
		warnings=warnings,
		documents=document_results,
	)


def _requirement_applies(requirement, vehicle):
	return (
		(not requirement.company or requirement.company == vehicle.company)
		and (
			not requirement.vehicle_category
			or requirement.vehicle_category == vehicle.vuna_vehicle_category
		)
		and (
			not requirement.ownership_type
			or requirement.ownership_type == vehicle.vuna_ownership_type
		)
	)


def _is_standard_insurance(requirement):
	return (requirement.document_type_name or requirement.name).strip().lower() == STANDARD_INSURANCE_TYPE


def _evaluate_standard_insurance(vehicle, requirement, evaluation_date):
	status = "Valid"
	reason = None
	warning = None
	if not vehicle.policy_no or (
		requirement.requires_expiry_date and not vehicle.end_date
	):
		status = "Missing"
		if requirement.mandatory_for_dispatch:
			reason = _reason(
				"INSURANCE_MISSING",
				_("Mandatory Vehicle insurance is not configured."),
				"Vehicle",
				vehicle.name,
			)
	elif vehicle.start_date and getdate(vehicle.start_date) > evaluation_date:
		status = "Not Yet Valid"
		if requirement.mandatory_for_dispatch:
			reason = _reason(
				"INSURANCE_NOT_YET_VALID",
				_("Vehicle insurance starts on {0}.").format(vehicle.start_date),
				"Vehicle",
				vehicle.name,
			)
	elif vehicle.end_date and getdate(vehicle.end_date) < evaluation_date:
		status = "Expired"
		if requirement.mandatory_for_dispatch:
			reason = _reason(
				"INSURANCE_EXPIRED",
				_("Mandatory Vehicle insurance expired on {0}.").format(
					vehicle.end_date
				),
				"Vehicle",
				vehicle.name,
			)
	elif vehicle.end_date and getdate(vehicle.end_date) <= add_days(
		evaluation_date, requirement.warning_days_before_expiry or 0
	):
		status = "Expiring Soon"
		warning = _reason(
			"INSURANCE_EXPIRING_SOON",
			_("Vehicle insurance expires on {0}.").format(vehicle.end_date),
			"Vehicle",
			vehicle.name,
		)
	return frappe._dict(
		document_type=requirement.name,
		source="Vehicle",
		document=None,
		status=status,
		expiry_date=vehicle.end_date,
		blocking_reason=reason,
		warning=warning,
	)


def _select_document_result(requirement, documents, evaluation_date):
	results = [
		_evaluate_document(document, requirement, evaluation_date)
		for document in documents
	]
	usable = next(
		(result for result in results if result.status in ("Valid", "Expiring Soon")),
		None,
	)
	selected = usable or (results[0] if results else None)
	if not selected:
		selected = frappe._dict(
			document_type=requirement.name,
			source="Vehicle Document",
			document=None,
			status="Missing",
			expiry_date=None,
			blocking_reason=None,
			warning=None,
		)

	if requirement.mandatory_for_dispatch and selected.status not in (
		"Valid",
		"Expiring Soon",
	):
		selected.blocking_reason = _reason(
			"DOCUMENT_MISSING" if selected.status == "Missing" else "DOCUMENT_INVALID",
			_("Mandatory {0} is {1}.").format(
				requirement.document_type_name, selected.status.lower()
			),
			"Vehicle Document" if selected.document else None,
			selected.document,
		)
	if selected.status == "Expiring Soon":
		selected.warning = _reason(
			"DOCUMENT_EXPIRING_SOON",
			_("{0} expires on {1}.").format(
				requirement.document_type_name, selected.expiry_date
			),
			"Vehicle Document",
			selected.document,
		)
	return selected


def _evaluate_document(document, requirement, evaluation_date):
	status = "Valid"
	if document.cancelled:
		status = "Cancelled"
	elif document.suspended:
		status = "Suspended"
	elif document.issue_date and getdate(document.issue_date) > evaluation_date:
		status = "Not Yet Valid"
	elif requirement.requires_expiry_date and not document.expiry_date:
		status = "Missing Expiry Date"
	elif document.expiry_date and getdate(document.expiry_date) < evaluation_date:
		status = "Expired"
	elif document.expiry_date and getdate(document.expiry_date) <= add_days(
		evaluation_date, requirement.warning_days_before_expiry or 0
	):
		status = "Expiring Soon"
	return frappe._dict(
		document_type=requirement.name,
		source="Vehicle Document",
		document=document.name,
		status=status,
		expiry_date=document.expiry_date,
		blocking_reason=None,
		warning=None,
	)


def _reason(code, message, reference_doctype=None, reference_name=None):
	return frappe._dict(
		code=code,
		message=message,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)


def _missing_vehicle_compliance(vehicle):
	reason = _reason(
		"VEHICLE_NOT_FOUND", _("Vehicle {0} does not exist.").format(vehicle)
	)
	return frappe._dict(
		vehicle=vehicle,
		compliant=False,
		status="Blocked",
		primary_reason=reason.message,
		reasons=[reason],
		warnings=[],
		documents=[],
	)
