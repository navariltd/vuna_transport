"""Permission-checked Delivery Trip execution actions for the mobile driver app."""

import re

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from vuna_transport.services.execution.lineage_service import get_delivery_trip_load_names
from vuna_transport.services.mobile.mobile_device_service import (
	_get_assigned_trip,
	_get_trip_stop,
	_require_mobile_driver,
)

INCIDENT_TYPES = (
	"Accident",
	"Breakdown",
	"Cargo Incident",
	"Delay / Disruption",
	"Driver Incident",
	"Mechanical Failure",
	"Security Incident",
	"Theft",
	"Tyre Failure",
	"Other",
)
INCIDENT_SEVERITIES = ("Low", "Medium", "High", "Critical")
REQUEST_ID_PATTERN = re.compile(r"^[a-f0-9-]{16,64}$", re.IGNORECASE)


def start_mobile_delivery_trip(installation_id, trip_name, device_identifier=None):
	"""Record the actual start of an assigned submitted trip."""
	driver, trip = _lock_assigned_trip(installation_id, trip_name, device_identifier)
	if trip.status == "Completed" or trip.vuna_actual_completion:
		frappe.throw(_("Delivery Trip {0} is already completed.").format(trip.name))
	if trip.vuna_actual_start:
		return _execution_response(trip)
	from vuna_transport.services.execution.transfer_execution_service import require_dispatch_stock_posted

	require_dispatch_stock_posted(trip)

	started_on = now_datetime()
	frappe.db.set_value(
		"Delivery Trip",
		trip.name,
		{"vuna_actual_start": started_on, "status": "In Transit"},
		update_modified=True,
	)
	trip.reload()
	if getattr(trip, "vuna_task_based", 0):
		from vuna_transport.services.execution.trip_task_service import sync_task_execution

		sync_task_execution(trip)
	trip.add_comment("Info", _("Trip started from the driver app by {0}.").format(driver.full_name))
	return _execution_response(trip)


def mark_mobile_stop_arrived(
	installation_id,
	trip_name,
	stop_name,
	device_identifier=None,
):
	"""Record arrival at one stop without marking its delivery as completed."""
	_, trip = _lock_assigned_trip(installation_id, trip_name, device_identifier)
	stop = _get_trip_stop(trip.name, stop_name)
	if stop.vuna_actual_arrival:
		return {"trip": _execution_response(trip)["trip"], "stop": _serialize_stop_execution(stop.name)}
	_require_started_trip(trip)
	if cint(stop.visited):
		frappe.throw(_("Delivery Stop {0} is already completed.").format(stop.name))
	frappe.db.set_value(
		"Delivery Stop",
		stop.name,
		"vuna_actual_arrival",
		now_datetime(),
		update_modified=False,
	)
	return {"trip": _execution_response(trip)["trip"], "stop": _serialize_stop_execution(stop.name)}


def complete_mobile_delivery_trip(installation_id, trip_name, device_identifier=None):
	"""Record final driver completion after ERPNext has completed every stop."""
	driver, trip = _lock_assigned_trip(installation_id, trip_name, device_identifier)
	if trip.vuna_actual_completion:
		return _execution_response(trip)
	_require_started_trip(trip)
	from vuna_transport.services.execution.trip_task_service import require_tasks_complete

	require_tasks_complete(trip)
	stops = _trip_stop_rows(trip.name)
	incomplete = [stop.name for stop in stops if not cint(stop.visited)]
	if incomplete:
		frappe.throw(
			_("Complete every Delivery Stop before completing the trip. Pending stops: {0}.").format(
				", ".join(incomplete)
			)
		)
	if not trip.vuna_actual_completion:
		frappe.db.set_value(
			"Delivery Trip",
			trip.name,
			{"vuna_actual_completion": now_datetime(), "status": "Completed"},
			update_modified=True,
		)
		trip.reload()
		trip.add_comment(
			"Info", _("Trip completion confirmed from the driver app by {0}.").format(driver.full_name)
		)
	return _execution_response(trip)


def synchronize_mobile_trip_execution_status(doc, method=None):
	"""Preserve explicit mobile execution state after ERPNext recalculates trip status."""
	if doc.docstatus != 1:
		return

	if getattr(doc, "vuna_actual_completion", None):
		expected_status = "Completed"
	elif getattr(doc, "vuna_actual_start", None) and not all(
		cint(stop.visited) for stop in (doc.delivery_stops or [])
	):
		expected_status = "In Transit"
	else:
		return

	if doc.status != expected_status:
		frappe.db.set_value(
			"Delivery Trip",
			doc.name,
			"status",
			expected_status,
			update_modified=False,
		)
		doc.status = expected_status


def get_mobile_trip_incidents(installation_id, trip_name, device_identifier=None):
	"""Return incidents from one currently assigned trip."""
	driver = _require_mobile_driver(installation_id, device_identifier)
	trip = _get_assigned_trip(trip_name, driver.name)
	incidents = frappe.get_all(
		"Vehicle Incident",
		filters={"delivery_trip": trip.name, "driver": driver.name, "docstatus": ["!=", 2]},
		fields=[
			"name",
			"incident_type",
			"incident_datetime",
			"severity",
			"description",
			"resolution_status",
			"resolution",
			"resolved_on",
			"vehicle_immobilized",
			"cargo_affected",
			"driver_affected",
			"latitude",
			"longitude",
			"docstatus",
		],
		order_by="incident_datetime desc",
		limit_page_length=100,
		ignore_permissions=True,
	)
	return {
		"trip": {"name": trip.name, "vehicle": _trip_vehicle(trip)},
		"incident_types": INCIDENT_TYPES,
		"severities": INCIDENT_SEVERITIES,
		"incidents": incidents,
	}


def report_mobile_trip_incident(
	installation_id,
	trip_name,
	payload,
	device_identifier=None,
):
	"""Create an open draft Vehicle Incident for an assigned active trip."""
	driver, trip = _lock_assigned_trip(installation_id, trip_name, device_identifier)
	_require_started_trip(trip)
	payload = frappe.parse_json(payload) if isinstance(payload, str) else (payload or {})
	request_id = _validate_request_id(payload.get("request_id"))
	existing = frappe.db.get_value(
		"Vehicle Incident",
		{"mobile_request_id": request_id, "owner": frappe.session.user, "docstatus": ["!=", 2]},
		"name",
	)
	if existing:
		return {"incident": existing, "created": False}

	incident_type = payload.get("incident_type")
	severity = payload.get("severity") or "Medium"
	description = (payload.get("description") or "").strip()
	if incident_type not in INCIDENT_TYPES:
		frappe.throw(_("Select a valid incident type."))
	if severity not in INCIDENT_SEVERITIES:
		frappe.throw(_("Select a valid incident severity."))
	if not description:
		frappe.throw(_("Describe what happened."))
	if len(description) > 4000:
		frappe.throw(_("Incident description cannot exceed 4,000 characters."))

	stop = None
	if payload.get("delivery_stop"):
		stop = _get_trip_stop(trip.name, payload.get("delivery_stop"))
	load_names = get_delivery_trip_load_names(trip)
	incident = frappe.get_doc(
		{
			"doctype": "Vehicle Incident",
			"vehicle": _trip_vehicle(trip),
			"company": trip.company,
			"driver": driver.name,
			"delivery_trip": trip.name,
			"delivery_load": getattr(stop, "vuna_delivery_load", None)
			or (load_names[0] if len(load_names) == 1 else None),
			"vehicle_allocation": getattr(trip, "vuna_current_vehicle_allocation", None)
			or getattr(trip, "vuna_vehicle_allocation", None),
			"incident_type": incident_type,
			"incident_datetime": now_datetime(),
			"severity": severity,
			"resolution_status": "Open",
			"description": description,
			"vehicle_immobilized": cint(payload.get("vehicle_immobilized")),
			"cargo_affected": cint(payload.get("cargo_affected")),
			"driver_affected": cint(payload.get("driver_affected")),
			"replacement_required": cint(payload.get("replacement_required")),
			"latitude": payload.get("latitude"),
			"longitude": payload.get("longitude"),
			"mobile_request_id": request_id,
		}
	)
	incident.insert(ignore_permissions=True)
	incident.add_comment("Info", _("Reported from the driver app by {0}.").format(driver.full_name))
	return {"incident": incident.name, "created": True}


def resolve_mobile_trip_incident(
	installation_id,
	trip_name,
	incident_name,
	resolution,
	device_identifier=None,
):
	"""Resolve and submit a driver-reported incident, freezing its operational record."""
	driver, trip = _lock_assigned_trip(installation_id, trip_name, device_identifier)
	incident = frappe.get_doc("Vehicle Incident", incident_name)
	if incident.delivery_trip != trip.name or incident.driver != driver.name:
		frappe.throw(
			_("Vehicle Incident {0} does not belong to your assigned trip.").format(incident.name),
			frappe.PermissionError,
		)
	if incident.docstatus == 1:
		return {"incident": incident.name, "status": incident.resolution_status, "submitted": True}
	if incident.docstatus != 0:
		frappe.throw(_("Cancelled Vehicle Incident {0} cannot be resolved.").format(incident.name))
	resolution = (resolution or "").strip()
	if not resolution:
		frappe.throw(_("Describe how the incident was resolved."))
	if len(resolution) > 4000:
		frappe.throw(_("Incident resolution cannot exceed 4,000 characters."))
	incident.resolution_status = "Resolved"
	incident.resolution = resolution
	incident.resolved_on = now_datetime()
	incident.flags.ignore_permissions = True
	incident.save(ignore_permissions=True)
	incident.submit()
	incident.add_comment("Info", _("Resolved from the driver app by {0}.").format(driver.full_name))
	return {"incident": incident.name, "status": incident.resolution_status, "submitted": True}


def _lock_assigned_trip(installation_id, trip_name, device_identifier=None):
	driver = _require_mobile_driver(installation_id, device_identifier)
	frappe.db.sql("select name from `tabDelivery Trip` where name = %s for update", trip_name)
	return driver, _get_assigned_trip(trip_name, driver.name)


def _require_started_trip(trip):
	if not getattr(trip, "vuna_actual_start", None):
		frappe.throw(_("Start Delivery Trip {0} before performing this action.").format(trip.name))
	if getattr(trip, "vuna_actual_completion", None):
		frappe.throw(_("Delivery Trip {0} is already completed.").format(trip.name))


def _trip_stop_rows(trip_name):
	return frappe.get_all(
		"Delivery Stop",
		filters={"parent": trip_name, "parenttype": "Delivery Trip"},
		fields=["name", "visited", "vuna_actual_arrival", "vuna_actual_departure"],
		order_by="idx asc",
		ignore_permissions=True,
	)


def get_mobile_execution_status(trip, stops=None):
	stops = stops if stops is not None else _trip_stop_rows(trip.name)
	if trip.docstatus == 2:
		return "Cancelled"
	if getattr(trip, "vuna_actual_completion", None) or (
		getattr(trip, "status", None) == "Completed" and stops and all(cint(stop.visited) for stop in stops)
	):
		return "Completed"
	if getattr(trip, "vuna_actual_start", None):
		return "In Transit"
	return "Scheduled"


def get_mobile_execution_actions(trip, stops=None):
	stops = stops if stops is not None else _trip_stop_rows(trip.name)
	status = get_mobile_execution_status(trip, stops)
	return {
		"can_start": status == "Scheduled",
		"can_report_incident": status == "In Transit",
	}


def _execution_response(trip):
	stops = _trip_stop_rows(trip.name)
	return {
		"trip": {
			"name": trip.name,
			"status": get_mobile_execution_status(trip, stops),
			"erpnext_status": trip.status,
			"actual_start": trip.vuna_actual_start,
			"actual_completion": trip.vuna_actual_completion,
			"actions": get_mobile_execution_actions(trip, stops),
		}
	}


def _serialize_stop_execution(stop_name):
	stop = frappe.db.get_value(
		"Delivery Stop",
		stop_name,
		["name", "visited", "vuna_actual_arrival", "vuna_actual_departure"],
		as_dict=True,
	)
	return {
		**stop,
		"can_arrive": not cint(stop.visited) and not stop.vuna_actual_arrival,
		"can_complete_delivery": not cint(stop.visited) and bool(stop.vuna_actual_arrival),
	}


def _trip_vehicle(trip):
	vehicle = getattr(trip, "vuna_current_vehicle", None) or trip.vehicle
	if not vehicle:
		frappe.throw(_("Delivery Trip {0} has no assigned Vehicle.").format(trip.name))
	return vehicle


def _validate_request_id(request_id):
	if not request_id or not REQUEST_ID_PATTERN.match(str(request_id)):
		frappe.throw(_("A valid mobile request ID is required."))
	return str(request_id)
