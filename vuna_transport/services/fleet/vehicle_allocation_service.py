"""Vehicle allocation validation, activation, and history services."""

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime

from vuna_transport.services.execution.dispatch_guard_service import (
	lock_document,
	set_delivery_load_status,
	validate_load_is_operational,
)
from vuna_transport.services.demand.delivery_method_service import (
	validate_delivery_method_allows_vehicle_allocation,
)
from vuna_transport.services.shared.security_service import (
	require_doctype_permission,
	require_document_permission,
)
from vuna_transport.services.fleet.driver_availability_service import (
	evaluate_driver_availability,
)
from vuna_transport.services.shared.resource_scheduling_service import time_windows_overlap
from vuna_transport.services.fleet.vehicle_availability_service import (
	evaluate_vehicle_availability,
)
from vuna_transport.services.shared.workflow_policy_service import (
	allocation_is_required_before_delivery_notes,
)


ACTIVE_STATUSES = ("Allocated", "Accepted")


@frappe.whitelist()
def make_vehicle_allocation_from_load(source_name):
	require_doctype_permission("Vehicle Allocation", "create")
	lock_document("Delivery Load", source_name)
	load = frappe.get_doc("Delivery Load", source_name)
	require_document_permission(load, "write")
	validate_load_is_operational(load, _("allocating a vehicle"))
	_validate_planning_window(load)
	_validate_initial_allocation_sequence(load)
	existing_draft = frappe.db.get_value(
		"Vehicle Allocation",
		{"delivery_load": load.name, "docstatus": 0},
		"name",
		order_by="creation desc",
	)
	if existing_draft:
		return frappe.get_doc("Vehicle Allocation", existing_draft)
	allocation = frappe.new_doc("Vehicle Allocation")
	allocation.delivery_load = load.name
	allocation.company = load.company
	allocation.required_vehicle_category = load.required_vehicle_category
	allocation.planned_reporting_time = load.planned_departure
	allocation.allocation_type = "Owned Fleet"
	return allocation


@frappe.whitelist()
def make_replacement_allocation_from_load(source_name):
	"""Prepare an auditable replacement for the load's current allocation."""
	return make_replacement_allocation(source_name)


@frappe.whitelist()
def make_replacement_allocation_from_trip(source_name):
	trip = frappe.get_doc("Delivery Trip", source_name)
	require_document_permission(trip, "read")
	if not trip.vuna_delivery_load:
		frappe.throw(_("Delivery Trip {0} is not linked to a Delivery Load.").format(trip.name))
	return make_replacement_allocation(trip.vuna_delivery_load)


def make_replacement_allocation(delivery_load):
	"""Build a replacement through the canonical reallocation workflow."""
	return _make_replacement_allocation(delivery_load)


def _make_replacement_allocation(delivery_load):
	require_doctype_permission("Vehicle Allocation", "create")
	lock_document("Delivery Load", delivery_load)
	load = frappe.get_doc("Delivery Load", delivery_load)
	require_document_permission(load, "write")
	validate_load_is_operational(load, _("reallocating a vehicle"))
	_validate_planning_window(load)
	previous = get_active_allocation(load, required=True)
	trip = _get_active_trip(load)
	_validate_trip_allows_reallocation(trip)
	existing_draft = frappe.db.get_value(
		"Vehicle Allocation",
		{
			"delivery_load": load.name,
			"replacement_of": previous.name,
			"docstatus": 0,
		},
		"name",
		order_by="creation desc",
	)
	if existing_draft:
		return frappe.get_doc("Vehicle Allocation", existing_draft)

	allocation = frappe.new_doc("Vehicle Allocation")
	allocation.delivery_load = load.name
	allocation.company = load.company
	allocation.required_vehicle_category = load.required_vehicle_category
	allocation.planned_reporting_time = load.planned_departure
	allocation.allocation_type = previous.allocation_type
	allocation.assignment_event = "Reallocation"
	allocation.replacement_of = previous.name
	allocation.replacement_stage = _get_replacement_stage(trip)
	if previous.allocation_type == "Subcontractor":
		allocation.supplier = previous.supplier
		allocation.subcontractor_vehicle_category = previous.subcontractor_vehicle_category
	return allocation


def validate_allocation(allocation):
	lock_document("Delivery Load", allocation.delivery_load)
	load = frappe.get_doc("Delivery Load", allocation.delivery_load)
	require_document_permission(load, "write")
	validate_load_is_operational(load, _("allocating a vehicle"))
	_validate_planning_window(load)
	_validate_initial_allocation_sequence(load, allocation)
	trip = _get_active_trip(load)
	_validate_trip_allows_reallocation(trip)

	allocation.company = load.company
	allocation.required_vehicle_category = load.required_vehicle_category
	allocation.allocated_by = allocation.allocated_by or frappe.session.user
	allocation.allocated_on = allocation.allocated_on or now_datetime()
	allocation.planned_reporting_time = allocation.planned_reporting_time or load.planned_departure
	_set_assignment_context(allocation, load, trip)
	validate_delivery_method_allows_vehicle_allocation(load.delivery_method)

	if allocation.allocation_type == "Owned Fleet":
		_validate_owned_fleet(allocation, load)
	elif allocation.allocation_type == "Subcontractor":
		_validate_subcontractor(allocation, load)
	else:
		frappe.throw(_("Select a valid Allocation Type."))
	_validate_replacement_change(allocation)


def _set_assignment_context(allocation, load, trip):
	active_name = load.active_vehicle_allocation
	if active_name and active_name != allocation.name:
		if allocation.replacement_of and allocation.replacement_of != active_name:
			frappe.throw(
				_("The active Vehicle Allocation changed from {0} to {1}. Refresh and prepare a new replacement.").format(
					allocation.replacement_of, active_name
				)
			)
		allocation.assignment_event = "Reallocation"
		allocation.replacement_of = active_name
		allocation.replacement_stage = _get_replacement_stage(trip)
		if not allocation.replacement_reason:
			frappe.throw(_("Select a Replacement Reason."))
		if not (allocation.replacement_details or "").strip():
			frappe.throw(_("Describe why the vehicle or driver is being replaced."))
	else:
		allocation.assignment_event = "Initial Assignment"
		allocation.replacement_of = None
		allocation.replacement_stage = None
		allocation.replacement_reason = None
		allocation.replacement_details = None


def _validate_replacement_change(allocation):
	if allocation.assignment_event != "Reallocation" or not allocation.replacement_of:
		return
	previous = frappe.get_doc("Vehicle Allocation", allocation.replacement_of)
	if previous.delivery_load != allocation.delivery_load:
		frappe.throw(_("The replaced allocation belongs to a different Delivery Load."))
	if previous.docstatus != 1 or previous.status not in ACTIVE_STATUSES:
		frappe.throw(_("Vehicle Allocation {0} is no longer active. Refresh the load.").format(previous.name))
	if allocation.vehicle and allocation.vehicle == previous.vehicle and allocation.driver == previous.driver:
		frappe.throw(_("Select a different vehicle or driver for the replacement allocation."))


def _validate_owned_fleet(allocation, load):
	if not allocation.vehicle or not allocation.driver:
		frappe.throw(_("Vehicle and Driver are required for an Owned Fleet allocation."))

	lock_document("Vehicle", allocation.vehicle)
	lock_document("Driver", allocation.driver)
	vehicle = frappe.get_doc("Vehicle", allocation.vehicle)
	_validate_vehicle_category_for_load(load, vehicle.vuna_vehicle_category)
	_validate_vehicle_is_available(allocation, load)
	_validate_driver_is_available(allocation, load)


def _validate_subcontractor(allocation, load):
	if not allocation.vehicle:
		frappe.throw(
			_("Select the ERPNext Vehicle record for the subcontracted vehicle.")
		)
	lock_document("Vehicle", allocation.vehicle)
	vehicle = frappe.get_doc("Vehicle", allocation.vehicle)
	allocation.subcontractor_vehicle_category = vehicle.vuna_vehicle_category
	if getattr(vehicle, "vuna_supplier", None):
		allocation.supplier = vehicle.vuna_supplier
	_validate_vehicle_category_for_load(load, vehicle.vuna_vehicle_category)
	_validate_vehicle_is_available(allocation, load)
	if allocation.driver:
		lock_document("Driver", allocation.driver)
		_validate_driver_is_available(allocation, load)


def _validate_planning_window(load):
	if not load.planned_departure and not load.planned_arrival:
		return
	if not load.planned_departure or not load.planned_arrival:
		frappe.throw(
			_("Set both Planned Departure and Planned Arrival on Delivery Load {0}, or leave both blank.").format(
				load.name
			)
		)
	if get_datetime(load.planned_departure) >= get_datetime(load.planned_arrival):
		frappe.throw(_("Planned Arrival must be after Planned Departure."))


def get_load_resource_window(load):
	"""Return the interval used for resource checks.

	When no explicit planning window exists, resource lookups represent current
	availability only. A future availability claim requires explicit times.
	"""
	if load.planned_departure and load.planned_arrival:
		return load.planned_departure, load.planned_arrival
	if load.planned_departure or load.planned_arrival:
		_validate_planning_window(load)
	now = now_datetime()
	return now, add_to_date(now, seconds=1)


def _validate_initial_allocation_sequence(load, allocation=None):
	"""Enforce the configured order for the first assignment, not replacements."""
	if allocation_is_required_before_delivery_notes():
		return
	if load.active_vehicle_allocation or (
		allocation and getattr(allocation, "replacement_of", None)
	):
		return
	if load.delivery_method and not frappe.db.get_value(
		"Delivery Method", load.delivery_method, "requires_vehicle_allocation"
	):
		return

	expected_orders = {
		row.delivery_order for row in load.delivery_orders if row.delivery_order
	}
	covered_orders = set(
		frappe.get_all(
			"Delivery Note",
			filters={
				"vuna_delivery_load": load.name,
				"docstatus": ["!=", 2],
			},
			pluck="vuna_delivery_order",
		)
	)
	missing = sorted(expected_orders - covered_orders)
	if missing:
		frappe.throw(
			_(
				"Generate Delivery Notes for every Delivery Order before allocating a vehicle. Missing: {0}."
			).format(", ".join(missing))
		)


def _validate_vehicle_category_for_load(load, vehicle_category):
	if not vehicle_category:
		frappe.throw(_("Select a Vehicle Category for the allocated vehicle."))
	from vuna_transport.services.planning.delivery_load_service import validate_capacity

	validate_capacity(load, vehicle_category)


def _validate_vehicle_is_available(allocation, load):
	from_time, to_time = get_load_resource_window(load)
	result = evaluate_vehicle_availability(
		allocation.vehicle,
		from_time,
		to_time,
		delivery_load=load,
		exclude_allocation=allocation.name,
	)
	if not result.dispatch_eligible:
		frappe.throw(
			_("Vehicle {0} is unavailable: {1}").format(
				allocation.vehicle, result.primary_reason
			)
		)


def _validate_driver_is_available(allocation, load):
	from_time, to_time = get_load_resource_window(load)
	result = evaluate_driver_availability(
		allocation.driver,
		from_time,
		to_time,
		exclude_allocation=allocation.name,
		exclude_delivery_load=load.name,
		expected_company=load.company,
	)
	if not result.dispatch_eligible:
		frappe.throw(
			_("Driver {0} is unavailable: {1}").format(
				allocation.driver, result.primary_reason
			)
		)


def activate_allocation(allocation):
	lock_document("Delivery Load", allocation.delivery_load)
	load = frappe.get_doc("Delivery Load", allocation.delivery_load)
	previous = load.active_vehicle_allocation
	if previous and previous != allocation.name:
		_lock_allocation(previous)
		if allocation.replacement_of != previous:
			frappe.throw(_("The active allocation changed while this replacement was being prepared. Refresh and try again."))
		effective_from = allocation.effective_from or now_datetime()
		frappe.db.set_value(
			"Vehicle Allocation",
			previous,
			{
				"status": "Replaced",
				"effective_to": effective_from,
				"replaced_by": allocation.name,
			},
			update_modified=False,
		)
		load.add_comment(
			"Info",
			_("Vehicle Allocation {0} replaced {1}. Reason: {2}. Details: {3}").format(
				allocation.name,
				previous,
				allocation.replacement_reason,
				allocation.replacement_details,
			),
		)
	load.db_set("active_vehicle_allocation", allocation.name, update_modified=False)
	_sync_trip_execution_allocation(load, allocation)
	if load.status in ("Approved", "Proposed", "Under Review", "Awaiting Vehicle"):
		has_delivery_notes = frappe.db.exists(
			"Delivery Note",
			{"vuna_delivery_load": load.name, "docstatus": ["!=", 2]},
		)
		target_status = "Ready for Dispatch" if has_delivery_notes else "Allocated"
		set_delivery_load_status(
			load,
			target_status,
			_("Vehicle Allocation {0} submitted").format(allocation.name),
		)


def prepare_allocation_cancellation(allocation):
	"""Detach the active allocation before Frappe checks linked-document cancellation."""
	load = frappe.get_doc("Delivery Load", allocation.delivery_load)
	if load.active_vehicle_allocation == allocation.name:
		load.db_set("active_vehicle_allocation", None, update_modified=True)


def restore_previous_allocation(allocation, active_link_cleared=False):
	load = frappe.get_doc("Delivery Load", allocation.delivery_load)
	if not active_link_cleared and load.active_vehicle_allocation != allocation.name:
		return
	previous_name = allocation.replacement_of
	if previous_name:
		frappe.db.set_value(
			"Vehicle Allocation",
			previous_name,
			{"status": "Allocated", "effective_to": None, "replaced_by": None},
			update_modified=False,
		)
	load.db_set("active_vehicle_allocation", previous_name, update_modified=False)
	if previous_name:
		_sync_trip_execution_allocation(
			load, frappe.get_doc("Vehicle Allocation", previous_name), rollback=True
		)
	if not previous_name:
		has_delivery_notes = frappe.db.exists(
			"Delivery Note", {"vuna_delivery_load": load.name, "docstatus": ["!=", 2]}
		)
		target_status = "Awaiting Vehicle" if has_delivery_notes else "Approved"
		if load.status not in ("Completed", "Completed with Exceptions", "Dispatched"):
			set_delivery_load_status(
				load,
				target_status,
				_("Vehicle Allocation {0} cancelled").format(allocation.name),
			)


def validate_allocation_cancellation(allocation):
	"""Protect submitted history and prevent restoring a failed vehicle in transit."""
	if allocation.replaced_by:
		frappe.throw(
			_("Vehicle Allocation {0} is historical and was replaced by {1}; it cannot be cancelled.").format(
				allocation.name, allocation.replaced_by
			)
		)
	load = frappe.get_doc("Delivery Load", allocation.delivery_load)
	if load.active_vehicle_allocation != allocation.name:
		frappe.throw(_("Only the active Vehicle Allocation can be cancelled."))
	trip = _get_active_trip(load)
	if trip and trip.docstatus == 1:
		frappe.throw(
			_("An allocation used by an active trip cannot be cancelled. Create another reallocation instead.")
		)


def _get_active_trip(load):
	name = load.delivery_trip or frappe.db.get_value(
		"Delivery Trip",
		{"vuna_delivery_load": load.name, "docstatus": ["!=", 2]},
		"name",
	)
	if not name:
		return None
	return frappe.db.get_value(
		"Delivery Trip", name, ["name", "docstatus", "status"], as_dict=True
	)


def _validate_trip_allows_reallocation(trip):
	if trip and (trip.docstatus == 2 or trip.status in ("Completed", "Cancelled")):
		frappe.throw(_("Vehicle reallocation is not allowed after the Delivery Trip is completed or cancelled."))


def _get_replacement_stage(trip):
	return "In Transit" if trip and trip.status == "In Transit" else "Before Departure"


def _sync_trip_execution_allocation(load, allocation, rollback=False):
	trip = _get_active_trip(load)
	if not trip:
		return
	values = {
		"vuna_current_vehicle_allocation": allocation.name,
		"vuna_current_vehicle": allocation.vehicle,
		"vuna_current_driver": allocation.driver,
	}
	if trip.docstatus == 0 or trip.status == "Scheduled":
		values.update(
			{
				"vehicle": allocation.vehicle,
				"driver": allocation.driver,
				"vuna_vehicle_allocation": allocation.name,
			}
		)
	current_count = frappe.db.get_value(
		"Delivery Trip", trip.name, "vuna_reallocation_count"
	) or 0
	if allocation.assignment_event == "Reallocation" or rollback:
		values["vuna_reallocation_count"] = max(current_count + (-1 if rollback else 1), 0)
	frappe.db.set_value("Delivery Trip", trip.name, values, update_modified=True)
	frappe.get_doc("Delivery Trip", trip.name).add_comment(
		"Info",
		_("Execution allocation changed to {0}: vehicle {1}, driver {2}.").format(
			allocation.name,
			allocation.vehicle or allocation.subcontractor_vehicle_registration,
			allocation.driver or allocation.subcontractor_driver_name,
		),
	)


def _lock_allocation(name):
	frappe.db.sql(
		"select name from `tabVehicle Allocation` where name = %s for update", name
	)


def get_active_allocation(load, required=False):
	load = frappe.get_doc("Delivery Load", load) if isinstance(load, str) else load
	name = load.active_vehicle_allocation
	if name:
		allocation = frappe.get_doc("Vehicle Allocation", name)
		if allocation.docstatus == 1 and allocation.status in ACTIVE_STATUSES:
			return allocation
	if required:
		frappe.throw(_("Allocate a vehicle and driver to Delivery Load {0} first.").format(load.name))
	return None
