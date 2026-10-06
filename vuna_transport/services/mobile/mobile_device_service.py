import frappe
from frappe import _
from frappe.utils import now_datetime


def bootstrap_device(*, installation_id, device_identifier=None, platform=None, manufacturer=None, model_name=None, os_name=None, os_version=None, app_version=None):
	"""Register or validate the mobile installation for the logged-in user."""
	if frappe.session.user == "Guest":
		frappe.throw(_("You must be logged in before registering a mobile device."), frappe.AuthenticationError)
	if not installation_id:
		frappe.throw(_("Installation ID is required."))

	user = frappe.session.user
	device = _find_device(installation_id, device_identifier)
	device_name = device.name if device else None
	if device_name:
		device = frappe.get_doc("Transport Mobile Device", device_name)
		if device.user != user:
			return _device_response(device, allowed=False, message=_("This device is registered to another user. Contact your administrator."))
		if device.status != "Active":
			return _device_response(device, allowed=False, message=_("This device is {0}. Contact your administrator.").format(device.status.lower()))

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
			"name", "full_name", "status", "transporter", "employee", "cell_number",
			"license_number", "issuing_date", "expiry_date",
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


def _find_device(installation_id, device_identifier=None):
	filters = []
	if device_identifier:
		filters.append({"device_identifier": device_identifier})
	if installation_id:
		filters.extend([
			{"installation_id": installation_id},
			{"latest_installation_id": installation_id},
		])
	for device_filter in filters:
		device = frappe.db.get_value("Transport Mobile Device", device_filter, ["name", "user", "status"], as_dict=True)
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
	frappe.db.set_value("Transport Mobile Device", device.name, "last_seen_at", now_datetime(), update_modified=False)
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
