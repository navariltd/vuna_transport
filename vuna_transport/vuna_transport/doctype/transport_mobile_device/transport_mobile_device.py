# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now

from vuna_transport.services.mobile.mobile_device_service import _lock_mobile_user


class TransportMobileDevice(Document):
	def validate(self):
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
def activate_mobile_device(device_name):
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
def revoke_mobile_device(device_name):
	_require_device_administrator()
	device = frappe.get_doc("Transport Mobile Device", device_name)
	if device.status != "Revoked":
		device.db_set("status", "Revoked", update_modified=True)
		device.add_comment("Info", _("Device revoked by {0}.").format(frappe.session.user))
	return device.name
