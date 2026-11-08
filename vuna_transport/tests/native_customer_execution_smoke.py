"""Real ERPNext customer execution inside sales_transport_demand_smoke's rollback.

Only outbound notifications are suppressed. Stock, accounting, picking, mapping,
validation, trip and proof methods are real. Never invoke in operational work.
"""

from unittest.mock import patch

import frappe
from frappe.utils import add_days, now_datetime, today


def _quality(doc, kind):
	for row in doc.items:
		flag = (
			"inspection_required_before_delivery"
			if doc.doctype == "Delivery Note"
			else "inspection_required_before_purchase"
		)
		if not frappe.get_cached_value("Item", row.item_code, flag):
			continue
		inspection = frappe.get_doc(
			{
				"doctype": "Quality Inspection",
				"inspection_type": kind,
				"reference_type": doc.doctype,
				"reference_name": doc.name,
				"item_code": row.item_code,
				"sample_size": 1,
				"inspected_by": frappe.session.user,
				"status": "Accepted",
				"manual_inspection": 1,
			}
		).insert()
		inspection.submit()
	doc.reload()


def exercise(source, demand, load, consolidated=False, without_trip=False):
	from vuna_transport.services.execution.customer_reference_service import note_submission_status
	from vuna_transport.services.execution.pod_service import create_proof_of_delivery
	from vuna_transport.services.integrations.erpnext_integration import (
		create_delivery_notes_for_allocation,
		create_delivery_notes_for_load,
		create_delivery_trip_for_load,
		create_pick_list_for_load,
	)
	from vuna_transport.services.planning.transport_load_service import create_transport_load_from_demands

	item = frappe.get_cached_doc("Item", load.delivery_orders[0].item_code)
	assert not item.has_serial_no, "Serial identity is covered by separate identity tests"
	origin = frappe.get_doc("Warehouse", load.source_warehouse)
	warehouse = frappe.get_doc(
		{
			"doctype": "Warehouse",
			"warehouse_name": "Native rollback " + frappe.generate_hash(length=8),
			"company": load.company,
			"parent_warehouse": origin.parent_warehouse,
		}
	).insert()
	# Fixture-only isolation: use a new warehouse rather than existing operational stock.
	frappe.db.set_value(
		"Transport Load", load.name, "source_warehouse", warehouse.name, update_modified=False
	)
	load.reload()
	opening = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"company": load.company,
			"stock_entry_type": "Material Receipt",
			"purpose": "Material Receipt",
		}
	)
	batch = None
	if item.has_batch_no:
		batch = (
			frappe.get_doc(
				{
					"doctype": "Batch",
					"batch_id": "NATIVE-" + frappe.generate_hash(length=10),
					"item": item.name,
					"manufacturing_date": today(),
					"expiry_date": add_days(today(), 30),
				}
			)
			.insert()
			.name
		)
	opening.append(
		"items",
		{
			"item_code": item.name,
			"qty": 6,
			"uom": demand.items[0].transaction_uom,
			"stock_uom": item.stock_uom,
			"conversion_factor": demand.items[0].conversion_factor,
			"t_warehouse": warehouse.name,
			"basic_rate": 1,
			"use_serial_batch_fields": 1,
			"batch_no": batch,
		},
	)
	opening.insert()
	_quality(opening, "Incoming")
	opening.submit()
	second = frappe.get_doc(
		"Transport Load",
		create_transport_load_from_demands(
			[
				{
					"transport_demand": demand.name,
					"transport_demand_item": demand.items[0].name,
					"allocated_quantity": 4,
				}
			],
			source_warehouse=warehouse.name,
		),
	)
	second.submit()
	templates = frappe.get_all(
		"Vehicle Allocation",
		filters={"company": load.company, "docstatus": 1, "vehicle": ["is", "set"], "driver": ["is", "set"]},
		pluck="name",
		limit_page_length=1,
	)
	assert templates, "An allocation/driver template is needed for execution rehearsal"
	allocation = frappe.copy_doc(frappe.get_doc("Vehicle Allocation", templates[0]))
	allocation.docstatus, allocation.status = 1, "Allocated"
	allocation.planned_reporting_time = now_datetime()
	allocation.set("delivery_loads", [{"delivery_load": load.name}, {"delivery_load": second.name}])
	# Isolate scheduling/assignment, which has its own tests. Do not assign real work.
	allocation.db_insert()
	for row in allocation.delivery_loads:
		row.parent, row.parenttype, row.docstatus = allocation.name, "Vehicle Allocation", 1
		row.db_insert()
	for selected in (load, second):
		selected.db_set("active_vehicle_allocation", allocation.name, update_modified=False)
		selected.reload()
	if without_trip:
		# Fixture-only method variation, restored by the outer rollback.
		frappe.db.set_value("Delivery Method", load.delivery_method, "requires_delivery_trip", 0)

	def submit_note(selected):
		note = frappe.get_doc("Delivery Note", create_delivery_notes_for_load(selected)[0])
		return submit_created_note(note)

	def submit_created_note(note):
		assert note.vuna_transport_demand == demand.name and not note.vuna_delivery_order
		for row in note.items:
			if batch:
				row.use_serial_batch_fields, row.batch_no = 1, batch
		note.save()
		_quality(note, "Outgoing")
		note.submit()
		assert all(row.uom == demand.items[0].transaction_uom for row in note.items)
		return note

	note = submit_note(load)
	assert note_submission_status(load)["ready"]
	note.cancel()
	assert not note_submission_status(load)["ready"]
	load.reload()
	if consolidated:
		notes = create_delivery_notes_for_allocation(allocation)
		assert len(notes) == 1
		first = last = submit_created_note(frappe.get_doc("Delivery Note", notes[0]))
		assert len(first.items) == 2
	else:
		# Recreate through a real submitted Pick List, not a hand-built note.
		picking = create_pick_list_for_load(load)
		for row in picking.locations:
			if batch:
				row.use_serial_batch_fields, row.batch_no = 1, batch
		picking.save()
		picking.submit()
		first = submit_note(load)
		last = submit_note(second)
	assert (
		first.name != note.name
		and note_submission_status(load)["ready"]
		and note_submission_status(second)["ready"]
	)
	if without_trip:
		assert all(
			frappe.db.get_value("Transport Load", name, "status") == "Completed"
			for name in (load.name, second.name)
		)
		assert frappe.db.get_value("Transport Demand", demand.name, "status") == "Delivered"
		return {"no_trip_method_completed_from_notes": True, "batched": bool(batch)}
	trip = create_delivery_trip_for_load(load)
	with (
		patch("vuna_transport.services.mobile.mobile_notification_service.notify_delivery_trip"),
		patch("frappe.publish_realtime"),
	):
		trip.insert()
		trip.submit()
		trip.db_set({"vuna_actual_start": now_datetime(), "status": "In Transit"}, update_modified=False)
		proofs = []
		for stop in trip.delivery_stops:
			proof = frappe.get_doc("Proof of Delivery", create_proof_of_delivery(trip.name, stop.name))
			proof.received_by = "Rollback receiver"
			proof.delivery_datetime = now_datetime()
			if stop.delivery_note == first.name:
				partial_item = next(row for row in proof.items if row.ordered_qty == 2)
				partial_item.delivered_qty = 1
				partial_item.rejection_reason_remarks = "Packaging damaged in rollback rehearsal"
			proof.flags.ignore_mandatory = True  # Physical attachment capture is tested separately.
			proof.save()
			proof.submit()
			assert proof.transport_demand == demand.name and not proof.delivery_order
			assert all(
				row.transport_demand_item == demand.items[0].name and not row.delivery_order_item
				for row in proof.items
			)
			proofs.append(proof)
		partial = next(p for p in proofs if p.delivery_note == first.name)
		partial.reload()
		request = frappe.get_doc("Return Request", partial.return_request)
		assert (
			request.transport_demand == demand.name
			and request.items[0].transport_demand_item == demand.items[0].name
		)
		assert request.items[0].requested_return_qty == 1
		assert not request.delivery_order and not request.items[0].delivery_order_item
		assert frappe.db.get_value("Transport Load", load.name, "status") == "Completed with Exceptions"
		assert frappe.db.get_value("Transport Load", second.name, "status") == "Completed"
		from vuna_transport.services.execution.execution_service import get_load_execution_summary

		for name in (load.name, second.name):
			assert get_load_execution_summary(name)["proofs_of_delivery"]["submitted"] == 1
		if not consolidated:
			accepted = next(p for p in proofs if p.delivery_note == last.name)
			accepted.cancel()
			assert frappe.db.get_value("Transport Load", second.name, "status") == "Dispatched"
			trip.reload()
			assert not trip.vuna_actual_completion
		from vuna_transport.services.planning.demand_lifecycle_service import sync_demand_allocations

		sync_demand_allocations([demand.name])
		assert frappe.db.get_value("Transport Demand", demand.name, "status") == "Partially Delivered"
	# No driver push or ORM realtime jobs may escape a rolled-back fixture.
	frappe.db.after_commit.reset()
	return {
		"real_delivery_notes": True,
		"real_pick_list": not consolidated,
		"consolidated_notes": consolidated,
		"note_cancellation_reopens_readiness": True,
		"trip_and_pod": True,
		"rejected_pod_creates_native_return_request": True,
		"pod_cancellation_reopens_load": not consolidated,
		"batched": bool(batch),
	}
