import base64

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime
from frappe.utils.file_manager import save_file

from vuna_transport.services.execution.pod_service import create_proof_of_delivery


def bootstrap_device(
	*,
	installation_id,
	device_identifier=None,
	platform=None,
	manufacturer=None,
	model_name=None,
	os_name=None,
	os_version=None,
	app_version=None,
):
	"""Register or validate the mobile installation for the logged-in user."""
	if frappe.session.user == "Guest":
		frappe.throw(
			_("You must be logged in before registering a mobile device."), frappe.AuthenticationError
		)
	if not installation_id:
		frappe.throw(_("Installation ID is required."))

	user = frappe.session.user
	device = _find_device(installation_id, device_identifier)
	device_name = device.name if device else None
	if device_name:
		device = frappe.get_doc("Transport Mobile Device", device_name)
		if device.user != user:
			return _device_response(
				device,
				allowed=False,
				message=_("This device is registered to another user. Contact your administrator."),
			)
		if device.status != "Active":
			return _device_response(
				device,
				allowed=False,
				message=_("This device is {0}. Contact your administrator.").format(device.status.lower()),
			)

		device.last_seen_at = now_datetime()
		device.latest_installation_id = installation_id
		device.device_identifier = device_identifier or device.device_identifier
		device.platform = platform or device.platform
		device.app_version = app_version or device.app_version
		device.save(ignore_permissions=True)
		return _device_response(device, allowed=True)

	device = frappe.get_doc(
		{
			"doctype": "Transport Mobile Device",
			"installation_id": installation_id,
			"latest_installation_id": installation_id,
			"device_identifier": device_identifier,
			"platform": platform,
			"user": user,
			"status": "Active",
			"manufacturer": manufacturer,
			"model_name": model_name,
			"os_name": os_name,
			"os_version": os_version,
			"app_version": app_version,
			"first_registered_at": now_datetime(),
			"last_seen_at": now_datetime(),
		}
	)
	device.insert(ignore_permissions=True)
	return _device_response(device, allowed=True)


def get_current_driver_profile(installation_id, device_identifier=None):
	"""Return the ERPNext Driver linked to the authenticated mobile user."""
	device_error = _get_active_device(installation_id, device_identifier)
	if device_error:
		return device_error

	drivers = frappe.get_all(
		"Driver",
		filters={"user": frappe.session.user},
		fields=[
			"name",
			"full_name",
			"status",
			"transporter",
			"employee",
			"cell_number",
			"license_number",
			"issuing_date",
			"expiry_date",
		],
		limit_page_length=2,
		ignore_permissions=True,
	)
	if not drivers:
		return {
			"linked": False,
			"code": "DRIVER_NOT_LINKED",
			"message": _("Your Frappe user is not linked to an ERPNext Driver. Contact your administrator."),
		}
	if len(drivers) > 1:
		return {
			"linked": False,
			"code": "MULTIPLE_DRIVERS_LINKED",
			"message": _("Your Frappe user is linked to more than one Driver. Contact your administrator."),
		}

	driver = drivers[0]
	if driver.employee:
		driver.employee_details = frappe.db.get_value(
			"Employee",
			driver.employee,
			["employee_name", "company", "department", "designation"],
			as_dict=True,
		)
	return {"linked": True, "driver": driver}


def get_assigned_delivery_trips(installation_id, device_identifier=None):
	"""Return submitted Delivery Trips assigned to the authenticated Driver."""
	profile = get_current_driver_profile(installation_id, device_identifier)
	if not profile.get("linked"):
		return profile

	driver_name = profile["driver"]["name"]
	trip_filters = {"docstatus": 1}
	trip_or_filters = [{"driver": driver_name}]
	if frappe.get_meta("Delivery Trip").has_field("vuna_current_driver"):
		trip_or_filters.append({"vuna_current_driver": driver_name})

	trips = frappe.get_all(
		"Delivery Trip",
		filters=trip_filters,
		or_filters=trip_or_filters,
		fields=["name", "status", "company", "vehicle", "driver", "departure_time", "total_distance", "uom"],
		order_by="departure_time asc, creation asc",
		limit_page_length=100,
		ignore_permissions=True,
	)
	if not trips:
		return {"linked": True, "driver": profile["driver"], "trips": []}

	stop_rows = frappe.get_all(
		"Delivery Stop",
		filters={"parent": ["in", [trip.name for trip in trips]], "parenttype": "Delivery Trip"},
		fields=_delivery_stop_fields(include_parent=True),
		order_by="parent asc, idx asc",
		limit_page_length=1000,
		ignore_permissions=True,
	)
	stops_by_trip = {}
	for stop in stop_rows:
		stops_by_trip.setdefault(stop.parent, []).append(stop)
	for trip in trips:
		trip["stops"] = stops_by_trip.get(trip.name, [])
		trip["stop_count"] = len(trip["stops"])
		trip["completed_stop_count"] = sum(cint(stop.visited) for stop in trip["stops"])

	return {"linked": True, "driver": profile["driver"], "trips": trips}


def get_delivery_trip_details(installation_id, trip_name, device_identifier=None):
	"""Return one submitted trip and its ordered stops for the assigned mobile Driver."""
	driver = _require_mobile_driver(installation_id, device_identifier)
	trip = _get_assigned_trip(trip_name, driver.name)
	stops = frappe.get_all(
		"Delivery Stop",
		filters={"parent": trip.name, "parenttype": "Delivery Trip"},
		fields=_delivery_stop_fields(include_parent=False),
		order_by="idx asc",
		limit_page_length=500,
		ignore_permissions=True,
	)
	_enrich_stops(stops)
	completed = sum(cint(stop.visited) for stop in stops)
	next_stop = next((stop.name for stop in stops if not cint(stop.visited)), None)
	return {
		"trip": {
			"name": trip.name,
			"status": trip.status,
			"company": trip.company,
			"vehicle": trip.vehicle,
			"driver": trip.driver,
			"departure_time": trip.departure_time,
			"total_distance": trip.total_distance,
			"uom": trip.uom,
			"stop_count": len(stops),
			"completed_stop_count": completed,
			"next_stop": next_stop,
			"stops": stops,
		}
	}


def get_delivery_note_details(installation_id, trip_name, stop_name, device_identifier=None):
	"""Return the Delivery Note and customer context for one assigned trip stop."""
	driver = _require_mobile_driver(installation_id, device_identifier)
	trip = _get_assigned_trip(trip_name, driver.name)
	stop = _get_trip_stop(trip.name, stop_name)
	if not stop.delivery_note:
		frappe.throw(_("Delivery Stop {0} has no Delivery Note.").format(stop.name))

	delivery_note = frappe.get_doc("Delivery Note", stop.delivery_note)
	if delivery_note.docstatus != 1:
		frappe.throw(_("Delivery Note {0} is not submitted.").format(delivery_note.name))
	_enrich_stops([stop])
	return {
		"trip": {"name": trip.name, "status": trip.status, "vehicle": trip.vehicle},
		"stop": stop,
		"delivery_note": {
			"name": delivery_note.name,
			"customer": delivery_note.customer,
			"customer_name": delivery_note.customer_name,
			"posting_date": delivery_note.posting_date,
			"posting_time": delivery_note.posting_time,
			"status": delivery_note.status,
			"currency": delivery_note.currency,
			"grand_total": delivery_note.grand_total,
			"items": [
				{
					"name": row.name,
					"item_code": row.item_code,
					"item_name": row.item_name,
					"description": row.description,
					"qty": row.qty,
					"uom": row.uom,
					"stock_qty": row.stock_qty,
					"stock_uom": row.stock_uom,
					"batch_no": getattr(row, "batch_no", None),
				}
				for row in delivery_note.items
			],
		},
	}


def prepare_mobile_proof_of_delivery(installation_id, trip_name, stop_name, device_identifier=None):
	"""Create or load the single active draft POD for an assigned stop."""
	driver = _require_mobile_driver(installation_id, device_identifier)
	trip = _get_assigned_trip(trip_name, driver.name)
	_get_trip_stop(trip.name, stop_name)
	pod_name = create_proof_of_delivery(trip.name, stop_name)
	return _serialize_pod(frappe.get_doc("Proof of Delivery", pod_name))


def submit_mobile_proof_of_delivery(
	installation_id,
	trip_name,
	stop_name,
	payload,
	device_identifier=None,
):
	"""Apply a mobile POD payload and submit it through the normal DocType workflow."""
	driver = _require_mobile_driver(installation_id, device_identifier)
	trip = _get_assigned_trip(trip_name, driver.name)
	_get_trip_stop(trip.name, stop_name)
	payload = frappe.parse_json(payload) if isinstance(payload, str) else (payload or {})
	pod_name = payload.get("name") or create_proof_of_delivery(trip.name, stop_name)
	pod = frappe.get_doc("Proof of Delivery", pod_name)
	if pod.delivery_trip != trip.name or pod.delivery_stop != stop_name:
		frappe.throw(_("Proof of Delivery {0} does not belong to this stop.").format(pod.name))
	if pod.docstatus != 0:
		frappe.throw(_("Proof of Delivery {0} has already been submitted.").format(pod.name))

	pod.received_by = payload.get("received_by")
	pod.receiver_phone = payload.get("receiver_phone")
	pod.delivery_datetime = now_datetime()
	pod.exception_reason = payload.get("exception_reason")
	pod.remarks = payload.get("remarks")
	pod.notes = payload.get("notes")
	pod.signature = payload.get("signature")
	pod.delivery_photo = payload.get("delivery_photo")
	item_values = {row.get("name"): row for row in payload.get("items", []) if row.get("name")}
	for item in pod.items:
		values = item_values.get(item.name)
		if not values:
			frappe.throw(
				_("Proof of Delivery item {0} is missing from the submission.").format(item.item_code)
			)
		item.delivered_qty = flt(values.get("delivered_qty"))
		item.rejection_reason = values.get("rejection_reason")
		item.rejection_reason_remarks = values.get("rejection_reason_remarks")

	pod.save()
	pod.submit()
	return {
		"proof_of_delivery": pod.name,
		"status": pod.status,
		"trip": get_delivery_trip_details(
			installation_id,
			trip.name,
			device_identifier,
		)["trip"],
	}


def upload_mobile_pod_attachment(
	installation_id,
	trip_name,
	stop_name,
	pod_name,
	attachment_type,
	content,
	filename,
	device_identifier=None,
	content_encoding="base64",
):
	"""Attach one private signature or delivery photo to an assigned draft POD."""
	driver = _require_mobile_driver(installation_id, device_identifier)
	trip = _get_assigned_trip(trip_name, driver.name)
	_get_trip_stop(trip.name, stop_name)
	pod = frappe.get_doc("Proof of Delivery", pod_name)
	if pod.delivery_trip != trip.name or pod.delivery_stop != stop_name or pod.docstatus != 0:
		frappe.throw(_("Attachments can only be added to this stop's draft Proof of Delivery."))
	fieldname = {"signature": "signature", "delivery_photo": "delivery_photo"}.get(attachment_type)
	if not fieldname:
		frappe.throw(_("Unsupported Proof of Delivery attachment type."))
	if not content:
		frappe.throw(_("Attachment content is required."))
	try:
		file_content = (
			base64.b64decode(content, validate=True)
			if content_encoding == "base64"
			else content.encode("utf-8")
		)
	except (ValueError, TypeError):
		frappe.throw(_("The attachment data is invalid."))
	if len(file_content) > 8 * 1024 * 1024:
		frappe.throw(_("Proof of Delivery attachments cannot exceed 8 MB."))
	file_doc = save_file(filename, file_content, "Proof of Delivery", pod.name, is_private=1)
	pod.db_set(fieldname, file_doc.file_url, update_modified=False)
	return {"file_url": file_doc.file_url, "fieldname": fieldname}


def _require_mobile_driver(installation_id, device_identifier=None):
	profile = get_current_driver_profile(installation_id, device_identifier)
	if not profile.get("linked"):
		frappe.throw(profile.get("message") or _("A Driver profile is required."), frappe.PermissionError)
	return frappe._dict(profile["driver"])


def _get_assigned_trip(trip_name, driver_name):
	if not trip_name:
		frappe.throw(_("Delivery Trip is required."))
	trip = frappe.get_doc("Delivery Trip", trip_name)
	if trip.docstatus != 1:
		frappe.throw(_("Delivery Trip {0} is not submitted.").format(trip.name))
	assigned_drivers = {trip.driver}
	if trip.meta.has_field("vuna_current_driver"):
		assigned_drivers.add(trip.vuna_current_driver)
	if driver_name not in assigned_drivers:
		frappe.throw(
			_("Delivery Trip {0} is not assigned to your Driver profile.").format(trip.name),
			frappe.PermissionError,
		)
	return trip


def _get_trip_stop(trip_name, stop_name):
	stop = frappe.db.get_value(
		"Delivery Stop",
		{"name": stop_name, "parent": trip_name, "parenttype": "Delivery Trip"},
		_delivery_stop_fields(include_parent=False),
		as_dict=True,
	)
	if not stop:
		frappe.throw(
			_("Delivery Stop {0} does not belong to Delivery Trip {1}.").format(stop_name, trip_name)
		)
	return stop


def _delivery_stop_fields(*, include_parent):
	fields = [
		"name",
		"idx",
		"customer",
		"address",
		"customer_address",
		"contact",
		"customer_contact",
		"delivery_note",
		"visited",
		"estimated_arrival",
		"lat",
		"lng",
		"details",
	]
	if include_parent:
		fields.append("parent")
	meta = frappe.get_meta("Delivery Stop")
	for fieldname in (
		"vuna_delivery_status",
		"vuna_actual_arrival",
		"vuna_actual_departure",
		"vuna_proof_of_delivery",
	):
		if meta.has_field(fieldname):
			fields.append(fieldname)
	return fields


def _enrich_stops(stops):
	address_names = {stop.address for stop in stops if stop.address}
	contact_names = {stop.contact for stop in stops if stop.contact}
	address_fields = [
		"name",
		"address_title",
		"address_line1",
		"address_line2",
		"city",
		"county",
		"state",
		"country",
		"pincode",
		"phone",
		"email_id",
	]
	address_meta = frappe.get_meta("Address")
	for fieldname in ("vuna_latitude", "vuna_longitude"):
		if address_meta.has_field(fieldname):
			address_fields.append(fieldname)
	addresses = (
		{
			row.name: row
			for row in frappe.get_all(
				"Address",
				filters={"name": ["in", list(address_names)]},
				fields=address_fields,
				ignore_permissions=True,
			)
		}
		if address_names
		else {}
	)
	contacts = (
		{
			row.name: row
			for row in frappe.get_all(
				"Contact",
				filters={"name": ["in", list(contact_names)]},
				fields=["name", "first_name", "last_name", "phone", "mobile_no", "email_id"],
				ignore_permissions=True,
			)
		}
		if contact_names
		else {}
	)
	pod_names = {
		getattr(stop, "vuna_proof_of_delivery", None)
		for stop in stops
		if getattr(stop, "vuna_proof_of_delivery", None)
	}
	pods = (
		{
			row.name: row
			for row in frappe.get_all(
				"Proof of Delivery",
				filters={"name": ["in", list(pod_names)], "docstatus": ["!=", 2]},
				fields=["name", "status", "docstatus", "delivery_datetime"],
				ignore_permissions=True,
			)
		}
		if pod_names
		else {}
	)
	for stop in stops:
		address = addresses.get(stop.address)
		stop["address_details"] = address
		stop["contact_details"] = contacts.get(stop.contact)
		stop["proof_of_delivery"] = pods.get(getattr(stop, "vuna_proof_of_delivery", None))
		stop["delivery_status"] = getattr(stop, "vuna_delivery_status", None) or (
			"Completed" if stop.visited else "Pending"
		)
		if not _has_map_coordinates(stop.lat, stop.lng) and address and _has_map_coordinates(
			address.get("vuna_latitude"), address.get("vuna_longitude")
		):
			stop["lat"] = flt(address.vuna_latitude)
			stop["lng"] = flt(address.vuna_longitude)
			stop["coordinate_source"] = "Address"


def _has_map_coordinates(latitude, longitude):
	if latitude in (None, "") or longitude in (None, ""):
		return False
	latitude = flt(latitude)
	longitude = flt(longitude)
	return -90 <= latitude <= 90 and -180 <= longitude <= 180 and (latitude, longitude) != (0, 0)


def _serialize_pod(pod):
	reasons = frappe.get_all(
		"Rejection Reason",
		filters={"disabled": 0} if frappe.get_meta("Rejection Reason").has_field("disabled") else None,
		pluck="name",
		order_by="name asc",
		ignore_permissions=True,
	)
	return {
		"name": pod.name,
		"docstatus": pod.docstatus,
		"status": pod.status,
		"delivery_trip": pod.delivery_trip,
		"delivery_stop": pod.delivery_stop,
		"delivery_note": pod.delivery_note,
		"customer": pod.customer,
		"delivery_address": pod.delivery_address,
		"delivery_datetime": pod.delivery_datetime,
		"received_by": pod.received_by,
		"receiver_phone": pod.receiver_phone,
		"exception_reason": pod.exception_reason,
		"remarks": pod.remarks,
		"notes": pod.notes,
		"signature": pod.signature,
		"delivery_photo": pod.delivery_photo,
		"rejection_reasons": reasons,
		"exception_reasons": [
			"Customer Closed",
			"Customer Unavailable",
			"Wrong Address",
			"Product Rejected",
			"Product Damaged",
			"Quantity Shortage",
			"Temperature Breach",
			"Payment Unavailable",
			"Vehicle Breakdown",
			"Road or Weather Disruption",
			"Other",
		],
		"items": [
			{
				"name": row.name,
				"item_code": row.item_code,
				"item_name": row.item_name,
				"ordered_qty": row.ordered_qty,
				"delivered_qty": row.delivered_qty,
				"rejected_qty": row.rejected_qty,
				"rejection_reason": row.rejection_reason,
				"rejection_reason_remarks": row.rejection_reason_remarks,
				"batch_no": row.batch_no,
			}
			for row in pod.items
		],
	}


def _find_device(installation_id, device_identifier=None):
	filters = []
	if device_identifier:
		filters.append({"device_identifier": device_identifier})
	if installation_id:
		filters.extend(
			[
				{"installation_id": installation_id},
				{"latest_installation_id": installation_id},
			]
		)
	for device_filter in filters:
		device = frappe.db.get_value(
			"Transport Mobile Device", device_filter, ["name", "user", "status"], as_dict=True
		)
		if device:
			return device
	return None


def _get_active_device(installation_id, device_identifier=None):
	if frappe.session.user == "Guest":
		frappe.throw(_("You must be logged in."), frappe.AuthenticationError)
	device = _find_device(installation_id, device_identifier)
	if not device or device.user != frappe.session.user:
		return {
			"linked": False,
			"code": "DEVICE_NOT_REGISTERED",
			"message": _("This device is not registered for the logged-in user."),
		}
	if device.status != "Active":
		return {
			"linked": False,
			"code": "DEVICE_NOT_ACTIVE",
			"message": _("This device is {0}. Contact your administrator.").format(device.status.lower()),
		}
	frappe.db.set_value(
		"Transport Mobile Device", device.name, "last_seen_at", now_datetime(), update_modified=False
	)
	return None


def _device_response(device, *, allowed, message=None):
	return {
		"allowed": allowed,
		"device": {
			"name": device.name,
			"installation_id": device.installation_id,
			"latest_installation_id": device.latest_installation_id,
			"device_identifier": device.device_identifier,
			"platform": device.platform,
			"user": device.user,
			"status": device.status,
		},
		"message": message,
	}
