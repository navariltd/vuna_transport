"""Queued push notifications for authenticated Vuna Transport mobile devices."""

from urllib.parse import quote

import frappe
import requests
from frappe import _


EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def notify_delivery_trip(doc, method=None):
	"""Notify the assigned driver when a Delivery Trip becomes available."""
	if not doc.driver or method not in {"on_submit", "on_update_after_submit"}:
		return
	if method == "on_update_after_submit":
		before = doc.get_doc_before_save()
		if before and before.driver == doc.driver:
			return
	user = frappe.db.get_value("Driver", doc.driver, "user")
	if user:
		_queue_push(
			[user],
			"New delivery trip assigned",
			f"Delivery Trip {doc.name} is ready for your attention.",
			f"/trips/{quote(doc.name, safe='')}",
		)


def notify_expense_claim(doc, method=None):
	"""Notify the mobile driver when their Expense Claim status changes."""
	if not doc.employee or "Mobile Request ID:" not in (doc.remark or ""):
		return
	before = doc.get_doc_before_save()
	if not before or _document_status(before) == _document_status(doc):
		return
	users = frappe.get_all("Driver", filters={"employee": doc.employee}, pluck="user", ignore_permissions=True)
	users = [user for user in users if user]
	if users:
		trip = doc.get("delivery_trip")
		route = f"/trips/{quote(trip, safe='')}/requests" if trip else "/requests"
		_queue_push(users, "Expense Claim update", f"{doc.name} is now {_document_status(doc)}.", route)


def notify_material_request(doc, method=None):
	"""Notify the mobile driver when their Material Request status changes."""
	if "Mobile Request ID:" not in (doc.terms or ""):
		return
	before = doc.get_doc_before_save()
	if not before or _document_status(before) == _document_status(doc):
		return
	trip = _context_value(doc.terms, "Delivery Trip")
	route = f"/trips/{quote(trip, safe='')}/requests" if trip else "/requests"
	_queue_push(
		[doc.owner],
		"Material Request update",
		f"{doc.name} is now {_document_status(doc)}.",
		route,
	)


def _document_status(doc):
	return doc.get("workflow_state") or doc.get("approval_status") or doc.get("status") or "Updated"


def _context_value(value, label):
	for line in str(value or "").replace("<br>", "\n").splitlines():
		prefix = f"{label}:"
		if line.strip().startswith(prefix):
			return line.strip()[len(prefix) :].strip()
	return None


def _queue_push(users, title, body, route):
	for user in set(users):
		frappe.enqueue(
			"vuna_transport.services.mobile.mobile_notification_service.send_push_notification",
			queue="short",
			user=user,
			title=title,
			body=body,
			route=route,
		)


def send_push_notification(user, title, body, route):
	"""Send one queued Expo notification and disable invalid device tokens."""
	devices = frappe.get_all(
		"Transport Mobile Device",
		filters={"user": user, "status": "Active", "push_notifications_enabled": 1},
		pluck="name",
		ignore_permissions=True,
	)
	for device_name in devices:
		device = frappe.get_doc("Transport Mobile Device", device_name)
		token = device.get_password("push_token", raise_exception=False)
		if not token or device.push_provider != "expo":
			continue
		try:
			response = requests.post(
				EXPO_PUSH_URL,
				json={
					"to": token,
					"title": title,
					"body": body,
					"data": {"route": route},
					"channelId": "transport",
				},
				timeout=10,
			)
			response.raise_for_status()
			result = response.json()
			if result.get("data", {}).get("status") == "error":
				message = result["data"].get("message", "")
				if "DeviceNotRegistered" in message:
					frappe.db.set_value(
						"Transport Mobile Device",
						device.name,
						"push_notifications_enabled",
						0,
						update_modified=False,
					)
		except Exception:
			frappe.log_error(frappe.get_traceback(), _("Mobile push notification failed"))
