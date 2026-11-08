# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, escape_html, now

from vuna_transport.services.mobile.mobile_device_service import _lock_mobile_user
from vuna_transport.services.shared.security_service import require_company_access

_DEVICE_REASSIGNMENT = object()


class TransportMobileDevice(Document):
	def validate(self):
		previous = self.get_doc_before_save()
		if previous and previous.user != self.user:
			_require_device_administrator()
			if self.flags.mobile_device_reassignment is not _DEVICE_REASSIGNMENT:
				frappe.throw(_("Use Reassign Device to change the registered user."))
		if self.status != "Active":
			return
		if frappe.db.exists(
			"Transport Mobile Device",
			{"user": self.user, "status": "Active", "name": ["!=", self.name]},
		):
			frappe.throw(_("User {0} already has an active Transport Mobile Device.").format(self.user))


def _require_device_administrator():
	if not {"System Manager", "Transport Manager"}.intersection(frappe.get_roles()):
		frappe.throw(
			_("Only a Transport Manager or System Manager can manage mobile devices."), frappe.PermissionError
		)


@frappe.whitelist()
def activate_mobile_device(device_name: str):
	"""Activate one device and revoke every other active device for its user."""
	_require_device_administrator()
	device = frappe.get_doc("Transport Mobile Device", device_name)
	_lock_mobile_user(device.user)
	frappe.db.sql(
		"""
		update `tabTransport Mobile Device`
		set status = 'Revoked', modified = %s, modified_by = %s
		where user = %s and status = 'Active' and name != %s
		""",
		(now(), frappe.session.user, device.user, device.name),
	)
	device.db_set("status", "Active", update_modified=True)
	device.add_comment("Info", _("Device activated by {0}.").format(frappe.session.user))
	return device.name


@frappe.whitelist()
def revoke_mobile_device(device_name: str):
	_require_device_administrator()
	device = frappe.get_doc("Transport Mobile Device", device_name)
	if device.status != "Revoked":
		device.db_set("status", "Revoked", update_modified=True)
		device.add_comment("Info", _("Device revoked by {0}.").format(frappe.session.user))
	return device.name


def _reassignment_companies(old_user, new_user):
	"""Use employee companies when available; subcontract drivers may have none."""
	if new_user == "Guest" or not frappe.db.get_value("User", new_user, "enabled"):
		frappe.throw(_("Select an enabled user for the new driver."))
	drivers = frappe.get_all(
		"Driver",
		filters={"user": new_user},
		fields=["name", "employee", "status"],
		limit_page_length=2,
	)
	if len(drivers) != 1 or drivers[0].status != "Active":
		frappe.throw(_("The new user must be linked to exactly one active Driver."))
	previous_drivers = frappe.get_all(
		"Driver", filters={"user": old_user}, fields=["name", "employee"], limit_page_length=0
	)
	old_companies = {
		company
		for driver in previous_drivers
		if driver.employee
		if (company := frappe.db.get_value("Employee", driver.employee, "company"))
	}
	new_company = (
		frappe.db.get_value("Employee", drivers[0].employee, "company") if drivers[0].employee else None
	)
	if old_companies and new_company and old_companies != {new_company}:
		frappe.throw(_("Reassign the device to a driver in the same company."))
	for company in sorted(old_companies | ({new_company} if new_company else set())):
		require_company_access(company)


@frappe.whitelist(methods=["POST"])
def reassign_mobile_device(
	device_name: str,
	new_user: str,
	expected_user: str,
	reason: str,
	local_data_cleared: bool = False,
):
	"""Transfer this physical device without deleting its registration or history."""
	_require_device_administrator()
	if not cint(local_data_cleared):
		frappe.throw(_("Confirm that pending work is synced and local app data is cleared first."))
	reason = (reason or "").strip()
	if not reason or len(reason) > 1000:
		frappe.throw(_("Enter a reassignment reason of up to 1,000 characters."))
	if not expected_user or not new_user or new_user == expected_user:
		frappe.throw(_("Select a different user for the new driver."))
	device = frappe.get_doc("Transport Mobile Device", device_name)
	device.check_permission("write")
	# Match bootstrap/activation's user lock. Sorted locking also serializes two
	# simultaneous transfers between the same users without lock inversion.
	for user in sorted({expected_user, new_user}):
		_lock_mobile_user(user)
	frappe.db.get_value("Transport Mobile Device", device.name, "name", for_update=True)
	device.reload()
	if device.user != expected_user:
		frappe.throw(_("The registered user changed. Reload the device before reassigning it."))
	_reassignment_companies(expected_user, new_user)
	replaced = frappe.get_all(
		"Transport Mobile Device",
		filters={"user": new_user, "status": "Active", "name": ["!=", device.name]},
		pluck="name",
	)
	for name in replaced:
		other = frappe.get_doc("Transport Mobile Device", name)
		other.check_permission("write")
		other.db_set("status", "Revoked", update_modified=True)
		other.add_comment(
			"Info",
			_("Replaced by device {0} through administrator reassignment.").format(escape_html(device.name)),
		)
	device.user, device.status = new_user, "Active"
	device.push_token = device.push_provider = device.push_token_updated_at = None
	device.push_notifications_enabled = 0
	device.last_seen_at = None
	device.latest_installation_id = None
	device.flags.mobile_device_reassignment = _DEVICE_REASSIGNMENT
	# Normal save preserves Frappe's Version trail and removes the old encrypted
	# Password field value. Do not bypass validation or commit outside the request.
	try:
		device.save()
	finally:
		device.flags.pop("mobile_device_reassignment", None)
	device.add_comment(
		"Info",
		_(
			"Device reassigned from {0} to {1} by {2}. Pending work sync and local app-data clearance confirmed. Reason: {3}"
		).format(
			escape_html(expected_user),
			escape_html(new_user),
			escape_html(frappe.session.user),
			escape_html(reason),
		),
	)
	return {"device": device.name, "user": new_user, "revoked_devices": replaced}
