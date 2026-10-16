"""QR-based loading and gate dispatch controls for Delivery Notes."""

import base64
import hashlib
import hmac
from io import BytesIO

import frappe
from frappe import _
from frappe.utils import now_datetime

from vuna_transport.services.shared.security_service import require_document_permission

READY_FOR_LOADING = "Ready for Loading"
LOADED = "Loaded"
DISPATCHED = "Dispatched"

SCAN_ACTIONS = ("Loaded", "Dispatched")


def _scan_role_field(action):
	return {
		"Loaded": "allowed_loaded_scan_roles",
		"Dispatched": "allowed_dispatched_scan_roles",
	}.get(action)


def _configured_scan_roles(action):
	fieldname = _scan_role_field(action)
	if not fieldname:
		return set()

	settings = frappe.get_single("Transport Settings")
	roles = {row.role for row in (getattr(settings, fieldname, None) or []) if row.role}
	return roles


@frappe.whitelist()
def get_available_scan_actions():
	"""Return scan actions permitted for the current user and settings."""
	user_roles = set(frappe.get_roles(frappe.session.user))
	return {action: bool(user_roles.intersection(_configured_scan_roles(action))) for action in SCAN_ACTIONS}


def _secret():
	secret = frappe.conf.get("encryption_key")
	if not secret:
		frappe.throw(_("The site encryption key is required for Delivery Note QR scanning."))
	return secret


def build_delivery_note_qr_token(delivery_note):
	"""Create a signed token for a Delivery Note without exposing an API credential."""
	name = delivery_note.name if hasattr(delivery_note, "name") else delivery_note
	payload = f"VUNA-DN-1:{name}"
	signature = hmac.new(_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
	return f"{payload}:{signature}"


def _resolve_qr_token(token):
	try:
		version, name, signature = token.split(":", 2)
	except (AttributeError, ValueError):
		frappe.throw(_("Invalid Delivery Note QR code."), frappe.ValidationError)

	payload = f"{version}:{name}"
	expected = hmac.new(_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
	if version != "VUNA-DN-1" or not hmac.compare_digest(signature, expected):
		frappe.throw(_("Invalid Delivery Note QR code."), frappe.ValidationError)
	return frappe.get_doc("Delivery Note", name)


def _require_scan_action_permission(action):
	allowed_roles = _configured_scan_roles(action)
	if not allowed_roles:
		frappe.throw(_("Unsupported Delivery Note scan action."))

	user_roles = set(frappe.get_roles(frappe.session.user))
	if not user_roles.intersection(allowed_roles):
		frappe.throw(
			_("You do not have permission to apply the {0} scan action.").format(action),
			frappe.PermissionError,
		)


@frappe.whitelist()
def scan_delivery_note_qr(token, action, device_reference=None, remarks=None):
	"""Apply one authenticated loading or gate-dispatch scan."""
	_require_scan_action_permission(action)
	doc = _resolve_qr_token(token)
	require_document_permission(doc, "read")
	if not frappe.has_permission("Delivery Note Dispatch Event", "create"):
		frappe.throw(_("You do not have permission to record Delivery Note scans."), frappe.PermissionError)

	if doc.docstatus != 1:
		frappe.throw(_("Delivery Note {0} must be submitted before it can be scanned.").format(doc.name))

	current_status = getattr(doc, "vuna_dispatch_status", None) or READY_FOR_LOADING
	transitions = {
		"Loaded": (READY_FOR_LOADING, LOADED),
		"Dispatched": (LOADED, DISPATCHED),
	}
	if action not in transitions:
		frappe.throw(_("Unsupported Delivery Note scan action."))
	previous_status, new_status = transitions[action]
	if current_status != previous_status:
		frappe.throw(
			_("Delivery Note {0} is {1}; it cannot be marked {2}.").format(
				doc.name, current_status, action.lower()
			)
		)

	event = frappe.get_doc(
		{
			"doctype": "Delivery Note Dispatch Event",
			"delivery_note": doc.name,
			"company": doc.company,
			"action": action,
			"previous_status": previous_status,
			"new_status": new_status,
			"scanned_by": frappe.session.user,
			"scanned_at": now_datetime(),
			"device_reference": device_reference,
			"remarks": remarks,
		}
	)
	event.insert(ignore_permissions=True)
	frappe.db.set_value(
		"Delivery Note",
		doc.name,
		{
			"vuna_dispatch_status": new_status,
			"vuna_dispatch_scan_count": (getattr(doc, "vuna_dispatch_scan_count", 0) or 0) + 1,
		},
		update_modified=True,
	)
	frappe.db.commit()

	return {
		"delivery_note": doc.name,
		"previous_status": previous_status,
		"status": new_status,
		"event": event.name,
	}


def get_delivery_note_qr_html(doc):
	"""Return an inline SVG QR image for the Vuna Delivery Note print format."""
	from pyqrcode import create as qrcreate

	stream = BytesIO()
	try:
		qrcreate(build_delivery_note_qr_token(doc)).svg(
			stream, scale=4, background="white", module_color="#111827"
		)
		svg = base64.b64encode(stream.getvalue()).decode()
	finally:
		stream.close()
	return f'<img alt="Delivery Note QR" src="data:image/svg+xml;base64,{svg}" style="width: 140px; height: 140px;">'
