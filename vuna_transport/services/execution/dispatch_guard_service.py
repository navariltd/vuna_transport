"""Transaction locks, lifecycle guards, and auditable Transport Load status changes."""

import frappe
from frappe import _

CLOSED_LOAD_STATUSES = ("Completed", "Completed with Exceptions", "Cancelled")
LOCKABLE_DOCTYPES = {"Transport Load", "Vehicle", "Driver"}

ALLOWED_LOAD_STATUS_TRANSITIONS = {
	"Draft": {
		"Proposed",
		"Under Review",
		"Approved",
		"Allocated",
		"Released to Warehouse",
		"Ready for Dispatch",
		"Cancelled",
	},
	"Proposed": {
		"Under Review",
		"Approved",
		"Awaiting Vehicle",
		"Allocated",
		"Released to Warehouse",
		"Ready for Dispatch",
		"Cancelled",
	},
	"Under Review": {
		"Proposed",
		"Approved",
		"Allocated",
		"Released to Warehouse",
		"Ready for Dispatch",
		"Cancelled",
	},
	"Approved": {
		"Awaiting Vehicle",
		"Allocated",
		"Released to Warehouse",
		"Ready for Dispatch",
		"Cancelled",
	},
	"Awaiting Vehicle": {"Approved", "Allocated", "Ready for Dispatch", "Cancelled"},
	"Allocated": {
		"Approved",
		"Awaiting Vehicle",
		"Released to Warehouse",
		"Loading",
		"Ready for Dispatch",
		"Cancelled",
	},
	"Released to Warehouse": {
		"Awaiting Vehicle",
		"Loading",
		"Ready for Dispatch",
		"Cancelled",
	},
	"Loading": {"Awaiting Vehicle", "Ready for Dispatch", "Cancelled"},
	"Ready for Dispatch": {
		"Awaiting Vehicle",
		"Dispatched",
		"Completed",
		"Cancelled",
	},
	"Dispatched": {
		"Completed",
		"Completed with Exceptions",
		"Exception Resolution",
	},
	"Exception Resolution": {"Dispatched", "Completed with Exceptions"},
	"Completed": {"Exception Resolution"},
	"Completed with Exceptions": {"Exception Resolution"},
	"Cancelled": set(),
}


def lock_document(doctype, name):
	"""Serialize dispatch decisions around one known operational record."""
	if doctype not in LOCKABLE_DOCTYPES:
		raise ValueError(f"Unsupported dispatch lock DocType: {doctype}")
	if not name:
		return
	table = frappe.qb.DocType(doctype)
	frappe.qb.from_(table).select(table.name).where(table.name == name).for_update().run()


def get_locked_delivery_load(load):
	name = load if isinstance(load, str) else load.name
	lock_document("Transport Load", name)
	return frappe.get_doc("Transport Load", name)


def validate_load_is_operational(load, operation):
	if load.docstatus != 1:
		frappe.throw(_("Submit Transport Load {0} before {1}.").format(load.name, operation))
	if load.status in CLOSED_LOAD_STATUSES:
		frappe.throw(
			_("Transport Load {0} is {1} and cannot be used for {2}.").format(
				load.name, load.status, operation
			)
		)


def set_delivery_load_status(load, status, reason=None, force=False):
	"""Apply a checked state transition and leave an audit comment."""
	name = load if isinstance(load, str) else load.name
	lock_document("Transport Load", name)
	load = frappe.get_doc("Transport Load", name)
	current = load.status or "Draft"
	if current == status:
		return
	if not force and not is_load_status_transition_allowed(current, status):
		frappe.throw(_("Transport Load {0} cannot move from {1} to {2}.").format(load.name, current, status))
	load.db_set("status", status, update_modified=False)
	load.status = status
	message = _("Transport Load status changed from {0} to {1}.").format(current, status)
	if reason:
		message = _("{0} Reason: {1}").format(message, reason)
	load.add_comment("Info", message)
	from vuna_transport.services.fleet.fleet_service import sync_related_vehicle_snapshots

	sync_related_vehicle_snapshots(load)


def is_load_status_transition_allowed(current, target):
	return current == target or target in ALLOWED_LOAD_STATUS_TRANSITIONS.get(current, set())


def validate_load_can_be_cancelled(load):
	trip_rows = frappe.db.sql(
		"""
			select ds.parent
			from `tabDelivery Stop` ds
			join `tabDelivery Trip` dt on dt.name = ds.parent
			where ds.parenttype = 'Delivery Trip'
			and ds.vuna_delivery_load = %s
			and dt.docstatus != 2
			limit 1
		""",
		load.name,
		as_dict=True,
	)
	if trip_rows:
		trip_name = trip_rows[0].get("parent") if isinstance(trip_rows[0], dict) else trip_rows[0].parent
		frappe.throw(
			_("Cancel Delivery Trip {0} before cancelling Transport Load {1}.").format(trip_name, load.name)
		)

	note_name = frappe.db.get_value(
		"Delivery Note",
		{"vuna_delivery_load": load.name, "docstatus": ["!=", 2]},
		"name",
	)
	if note_name:
		frappe.throw(
			_("Cancel Delivery Note {0} before cancelling Transport Load {1}.").format(note_name, load.name)
		)
