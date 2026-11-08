"""Rollback-only planning smoke; never submit trips or notify real drivers."""

import frappe
from frappe.utils import now_datetime

from vuna_transport.services.demand.transport_demand_service import (
	build_source_legs,
	create_transport_demands,
)
from vuna_transport.services.integrations.erpnext_integration import create_delivery_trip_for_load
from vuna_transport.services.planning.transport_load_service import create_transport_load_from_demands


def run(
	verify_dispatch=False,
	verify_proof=False,
	verify_cancel=False,
	verify_cascade=False,
	depot_fraction=0,
	accepted_fraction=0.5,
	verify_return_pickup=False,
	override_source=False,
	use_depot_fallback=False,
	verify_warehouse_receipt=False,
):
	frappe.db.savepoint("task_trip_smoke")
	try:
		for name in frappe.get_all(
			"Material Request",
			filters={
				"docstatus": 1,
				"material_request_type": "Material Transfer",
				"status": ["not in", ["Stopped", "Closed", "Cancelled"]],
			},
			pluck="name",
			limit_page_length=50,
		):
			source = frappe.get_doc("Material Request", name)
			if not all(
				row.from_warehouse or source.get("set_from_warehouse") for row in source.items
			) or not build_source_legs(source):
				continue
			demand_names = create_transport_demands("Material Request", name)
			demand = frappe.get_doc("Transport Demand", demand_names[0])
			if demand.docstatus != 0:
				continue
			demand.submit()
			# Temporary addresses are fixture data only; never assign or guess the
			# production warehouse's address. Dynamic Links are rolled back too.
			for warehouse in (demand.source_warehouse, demand.destination_warehouse):
				from vuna_transport.services.routing.location_service import get_warehouse_planning_data

				if not get_warehouse_planning_data(warehouse).get("address"):
					frappe.get_doc(
						{
							"doctype": "Address",
							"address_title": "Transport Planning Test",
							"address_type": "Shipping",
							"address_line1": "Test",
							"city": "Test",
							"country": "Kenya",
							"links": [{"link_doctype": "Warehouse", "link_name": warehouse}],
						}
					).insert()
			item = demand.items[0]
			loading_warehouse = demand.source_warehouse
			if override_source:
				original = frappe.get_doc("Warehouse", demand.source_warehouse)
				loading_warehouse = (
					frappe.get_doc(
						{
							"doctype": "Warehouse",
							"warehouse_name": "Rollback fulfillment " + frappe.generate_hash(length=8),
							"company": demand.company,
							"parent_warehouse": original.parent_warehouse,
							"warehouse_type": original.warehouse_type,
						}
					)
					.insert()
					.name
				)
			load = frappe.get_doc(
				"Transport Load",
				create_transport_load_from_demands(
					[
						{
							"transport_demand": demand.name,
							"transport_demand_item": item.name,
							"allocated_quantity": item.transaction_qty,
						}
					],
					source_warehouse=loading_warehouse,
				),
			)
			load.submit()
			templates = frappe.get_all(
				"Vehicle Allocation",
				filters={
					"docstatus": 1,
					"company": demand.company,
					"vehicle": ["is", "set"],
					"driver": ["is", "set"],
				},
				pluck="name",
				limit_page_length=1,
			)
			if not templates:
				return {
					"result": "not exercised",
					"reason": "No allocation template for isolated planning fixture",
				}
			# Bypass allocation scheduling only for this rollback fixture. Vehicle
			# allocation validation has its own regression suite. No real assignment
			# or trip is submitted, and no notification hook is invoked.
			allocation = frappe.copy_doc(frappe.get_doc("Vehicle Allocation", templates[0]))
			allocation.docstatus = 1
			allocation.status = "Allocated"
			allocation.planned_reporting_time = now_datetime()
			allocation.set("delivery_loads", [{"delivery_load": load.name}])
			allocation.db_insert()
			for row in allocation.delivery_loads:
				row.parent = allocation.name
				row.db_insert()
			load.db_set("active_vehicle_allocation", allocation.name)
			if override_source:
				# A fresh warehouse has no valuation history. Seed real disposable
				# stock before ERPNext maps/revalues dispatch from the chosen origin.
				seed = frappe.new_doc("Stock Entry")
				seed.company = load.company
				for cargo in load.delivery_orders:
					seed.append(
						"items",
						{
							"item_code": cargo.item_code,
							"qty": cargo.allocated_quantity,
							"uom": cargo.uom,
							"stock_uom": cargo.stock_uom,
							"conversion_factor": cargo.conversion_factor,
							"transfer_qty": cargo.allocated_stock_qty,
							"s_warehouse": loading_warehouse,
						},
					)
				_seed_dispatch_stock(seed)
			pretrip_stock = _prepare_allocation_stock(allocation, load)
			assert pretrip_stock.from_warehouse == loading_warehouse
			assert all(row.s_warehouse == loading_warehouse for row in pretrip_stock.items)
			if override_source:
				assert loading_warehouse != demand.source_warehouse
				assert (
					frappe.db.get_value("Transport Demand", demand.name, "source_warehouse")
					== demand.source_warehouse
				)
			trip = create_delivery_trip_for_load(load.name)
			trip.insert()
			tasks = frappe.get_all(
				"Transport Trip Task",
				filters={"delivery_trip": trip.name},
				fields=["name", "task_type", "delivery_stop"],
			)
			assert {task.task_type for task in tasks} == {"Transfer Receipt"}
			assert len(trip.delivery_stops) == 1
			assert all(not stop.delivery_note for stop in trip.delivery_stops)
			assert trip.delivery_stops[0].vuna_depot_names == demand.destination_warehouse
			assert trip.delivery_stops[0].vuna_stop_type == "Depot"
			assert not trip.delivery_stops[0].customer
			trip.save()
			assert frappe.db.count("Transport Trip Task", {"delivery_trip": trip.name}) == 1
			if verify_warehouse_receipt:
				from vuna_transport.tests.warehouse_receipt_smoke import exercise

				trip.db_set("docstatus", 1, update_modified=False)
				return exercise(trip, frappe.get_doc("Transport Trip Task", tasks[0].name), pretrip_stock)
			if verify_cancel:
				trip.db_set("docstatus", 1, update_modified=False)
				trip.cancel()
				assert frappe.db.get_value("Stock Entry", pretrip_stock.name, "docstatus") == 1
				assert frappe.db.get_value("Vehicle Allocation", allocation.name, "docstatus") == 1
				assert frappe.db.get_value("Transport Load", load.name, "docstatus") == 1
				assert not frappe.db.get_value("Transport Load", load.name, "delivery_trip")
				assert frappe.db.get_value("Transport Trip Task", tasks[0].name, "status") == "Cancelled"
				replacement = create_delivery_trip_for_load(load.name)
				replacement.insert()
				assert replacement.name != trip.name
				assert len(replacement.delivery_stops) == 1
				return {
					"trip_cancelled_without_reversing_loading_stock": "passed",
					"replacement_trip_reuses_loaded_stock": "passed",
					"rolled_back": True,
				}
			if verify_proof or verify_cascade:
				trip.db_set("docstatus", 1, update_modified=False)
				dispatch = frappe.get_doc(
					"Transport Trip Task",
					next(task.name for task in tasks if task.task_type == "Transfer Receipt"),
				)
				return _verify_proof_cycle(
					trip,
					dispatch,
					pretrip_stock,
					verify_cascade=verify_cascade,
					depot_fraction=depot_fraction,
					accepted_fraction=accepted_fraction,
					verify_return_pickup=verify_return_pickup,
					use_depot_fallback=use_depot_fallback,
				)
			if verify_dispatch:
				return {
					"pretrip_dispatch": "passed",
					"vehicle_transit_default": "passed",
					"requested_uom": pretrip_stock.items[0].uom,
					"rolled_back": True,
				}
			frappe.delete_doc("Delivery Trip", trip.name)
			assert not frappe.db.exists("Transport Trip Task", {"delivery_trip": trip.name})
			return {
				"transfer_trip_without_delivery_notes": "passed",
				"tasks": 1,
				"idempotency_and_draft_cleanup": "passed",
				"rolled_back": True,
			}
		return {"result": "not exercised", "reason": "No eligible Material Transfer Request"}
	finally:
		frappe.db.rollback(save_point="task_trip_smoke")


def _prepare_allocation_stock(allocation, load):
	from vuna_transport.services.execution.allocation_transfer_service import make_allocation_stock_entry

	transit = frappe.db.get_value(
		"Warehouse",
		{
			"company": allocation.company,
			"vehicle": allocation.vehicle,
			"warehouse_type": "Transit",
			"is_group": 0,
			"disabled": 0,
		},
		"name",
	)
	if not transit:
		if not frappe.db.exists("Warehouse Type", "Transit"):
			frappe.get_doc({"doctype": "Warehouse Type", "name": "Transit"}).insert()
		transit = (
			frappe.get_doc(
				{
					"doctype": "Warehouse",
					"company": allocation.company,
					"vehicle": allocation.vehicle,
					"warehouse_name": "Transit Fixture " + frappe.generate_hash(length=8),
					"warehouse_type": "Transit",
				}
			)
			.insert()
			.name
		)
	entry = make_allocation_stock_entry(allocation.name, load.name)
	assert entry.to_warehouse == transit
	if entry.meta.has_field("driver"):
		assert entry.driver == allocation.driver
	if "nl_vms" in frappe.get_installed_apps():
		from nl_vms.controllers.warehouse_type import (
			ALLOWED_WAREHOUSE_TYPE_DOCTYPE,
			ITEM_WAREHOUSE_TYPES_FIELD,
			_effective_warehouse_types_for_item,
		)

		for code in {row.item_code for row in entry.items}:
			item = frappe.get_doc("Item", code)
			for warehouse_type in set(_effective_warehouse_types_for_item(code, item.item_group)) | {
				"Transit"
			}:
				if not frappe.db.exists(
					ALLOWED_WAREHOUSE_TYPE_DOCTYPE,
					{
						"parent": code,
						"parenttype": "Item",
						"parentfield": ITEM_WAREHOUSE_TYPES_FIELD,
						"warehouse_type": warehouse_type,
					},
				):
					frappe.get_doc(
						{
							"doctype": ALLOWED_WAREHOUSE_TYPE_DOCTYPE,
							"parent": code,
							"parenttype": "Item",
							"parentfield": ITEM_WAREHOUSE_TYPES_FIELD,
							"warehouse_type": warehouse_type,
						}
					).db_insert()
	_seed_dispatch_stock(entry)
	entry.insert()
	_add_fixture_quality_inspections(entry)
	entry.submit()
	return entry


def _verify_dispatch(trip, verify_proof=False):
	"""Fixture-only submitted state: never submit trips/stock or notify drivers."""
	from vuna_transport.services.execution.transfer_execution_service import (
		make_dispatch_stock_entry,
		require_dispatch_stock_posted,
	)

	trip.db_set("docstatus", 1, update_modified=False)
	task_name = frappe.db.get_value(
		"Transport Trip Task", {"delivery_trip": trip.name, "task_type": "Transfer Dispatch"}, "name"
	)
	transit = frappe.db.get_value(
		"Warehouse",
		{"company": trip.company, "warehouse_type": "Transit", "disabled": 0, "is_group": 0},
		"name",
	)
	if not transit:
		if not frappe.db.exists("Warehouse Type", "Transit"):
			frappe.get_doc({"doctype": "Warehouse Type", "name": "Transit"}).insert()
		transit = (
			frappe.get_doc(
				{
					"doctype": "Warehouse",
					"company": trip.company,
					"warehouse_name": "Dispatch Smoke " + frappe.generate_hash(length=8),
					"warehouse_type": "Transit",
				}
			)
			.insert()
			.name
		)
	try:
		require_dispatch_stock_posted(trip)
	except frappe.ValidationError:
		pass
	else:
		raise AssertionError("Departure must be blocked without posted dispatch stock")
	entry = make_dispatch_stock_entry(task_name, transit)
	task = frappe.get_doc("Transport Trip Task", task_name)
	assert entry.add_to_transit and entry.stock_entry_type == "Material Transfer"
	assert len(entry.items) == len(task.items)
	for row, cargo in zip(entry.items, task.items, strict=True):
		assert row.uom == cargo.uom and row.qty == cargo.qty
		assert row.transfer_qty == cargo.stock_qty
	# Site-specific Item warehouse restrictions must permit Transit. Add that
	# permission only inside this rolled-back fixture; never alter live policy.
	if "nl_vms" in frappe.get_installed_apps():
		from nl_vms.controllers.warehouse_type import (
			ALLOWED_WAREHOUSE_TYPE_DOCTYPE,
			ITEM_WAREHOUSE_TYPES_FIELD,
			_effective_warehouse_types_for_item,
		)

		for code in {row.item_code for row in entry.items}:
			item = frappe.get_doc("Item", code)
			allowed = _effective_warehouse_types_for_item(code, item.item_group)
			if "Transit" not in allowed:
				for warehouse_type in set(allowed) | {"Transit"}:
					if not frappe.db.exists(
						ALLOWED_WAREHOUSE_TYPE_DOCTYPE,
						{
							"parent": code,
							"parenttype": "Item",
							"parentfield": ITEM_WAREHOUSE_TYPES_FIELD,
							"warehouse_type": warehouse_type,
						},
					):
						frappe.get_doc(
							{
								"doctype": ALLOWED_WAREHOUSE_TYPE_DOCTYPE,
								"parent": code,
								"parenttype": "Item",
								"parentfield": ITEM_WAREHOUSE_TYPES_FIELD,
								"warehouse_type": warehouse_type,
							}
						).db_insert()
	if verify_proof:
		_seed_dispatch_stock(entry)
	entry.insert()
	assert entry.docstatus == 0
	assert make_dispatch_stock_entry(task_name, transit).name == entry.name
	assert not frappe.db.exists("Stock Ledger Entry", {"voucher_no": entry.name})
	if verify_proof:
		return _verify_proof_cycle(trip, task, entry)
	return {
		"dispatch_draft": "passed",
		"requested_uom": entry.items[0].uom,
		"draft_reused": True,
		"departure_guard": "passed",
		"stock_posted": False,
		"rolled_back": True,
	}


def _seed_dispatch_stock(entry):
	"""Seed disposable stock for the rollback-only document-level transfer test."""
	from frappe.utils import add_days, today

	opening = frappe.new_doc("Stock Entry")
	opening.company = entry.company
	opening.stock_entry_type = opening.purpose = "Material Receipt"
	bundle_plans = []
	for row in entry.items:
		has_batch, has_serial = frappe.get_cached_value(
			"Item", row.item_code, ["has_batch_no", "has_serial_no"]
		)
		if has_serial:
			raise AssertionError(
				"This smoke fixture requires a non-serialized item; serial identity has isolated tests"
			)
		batch = None
		if has_batch:
			batch = (
				frappe.get_doc(
					{
						"doctype": "Batch",
						"batch_id": "PROOF-TEST-" + frappe.generate_hash(length=10),
						"item": row.item_code,
						"manufacturing_date": today(),
						"expiry_date": add_days(today(), 30),
					}
				)
				.insert()
				.name
			)
			second = (
				frappe.get_doc(
					{
						"doctype": "Batch",
						"batch_id": "PROOF-TEST-" + frappe.generate_hash(length=10),
						"item": row.item_code,
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
					"item_code": row.item_code,
					"qty": row.qty / 2,
					"uom": row.uom,
					"stock_uom": row.stock_uom,
					"conversion_factor": row.conversion_factor,
					"t_warehouse": row.s_warehouse,
					"basic_rate": 1,
					"use_serial_batch_fields": 1,
					"batch_no": second,
				},
			)
			bundle_plans.append((row, {batch: row.transfer_qty / 2, second: row.transfer_qty / 2}))
		opening.append(
			"items",
			{
				"item_code": row.item_code,
				"qty": row.qty / 2 if has_batch else row.qty,
				"uom": row.uom,
				"stock_uom": row.stock_uom,
				"conversion_factor": row.conversion_factor,
				"t_warehouse": row.s_warehouse,
				"basic_rate": 1,
				"use_serial_batch_fields": 1,
				"batch_no": batch,
			},
		)
	opening.insert()
	opening.submit()
	from erpnext.stock.serial_batch_bundle import SerialBatchCreation

	for row, batches in bundle_plans:
		bundle = SerialBatchCreation(
			frappe._dict(
				item_code=row.item_code,
				company=entry.company,
				warehouse=row.s_warehouse,
				voucher_type="Stock Entry",
				type_of_transaction="Outward",
				qty=-row.transfer_qty,
				do_not_submit=True,
			)
		).make_serial_and_batch_bundle(batch_nos=batches)
		row.serial_and_batch_bundle = bundle.name
		row.use_serial_batch_fields = 0
		row.batch_no = row.serial_no = None


def _verify_proof_cycle(
	trip,
	dispatch_task,
	stock,
	verify_cascade=False,
	depot_fraction=0,
	accepted_fraction=0.5,
	verify_return_pickup=False,
	use_depot_fallback=False,
):
	assert not frappe.db.exists("DocType", "Transport Execution Proof")
	assert not frappe.db.exists("DocType", "Transport Execution Proof Item")
	assert frappe.get_meta("Stock Entry").get_field("vuna_transport_proof").options == "Proof of Delivery"
	from unittest.mock import patch

	from vuna_transport.services.execution.transfer_proof_service import (
		make_receipt_stock_entry,
		make_transfer_proof,
	)
	from vuna_transport.services.mobile.mobile_device_service import get_delivery_trip_details
	from vuna_transport.services.mobile.mobile_trip_execution_service import start_mobile_delivery_trip

	if stock.docstatus == 0:
		_add_fixture_quality_inspections(stock)
		stock.submit()
	# Exercise the real mobile detail serializer with a Document, replacing only
	# device authentication for this isolated submitted-state fixture.
	with patch(
		"vuna_transport.services.mobile.mobile_device_service._require_mobile_driver",
		return_value=frappe._dict(name=trip.driver),
	):
		mobile = get_delivery_trip_details("rollback-fixture", trip.name)
		assert mobile["trip"]["name"] == trip.name
		assert any(stop.get("tasks") for stop in mobile["trip"]["stops"])
	from vuna_transport.services.execution.transfer_execution_service import require_dispatch_stock_posted

	require_dispatch_stock_posted(trip)
	assert not frappe.db.exists("Proof of Delivery", {"delivery_trip": trip.name})
	with patch(
		"vuna_transport.services.mobile.mobile_trip_execution_service._require_mobile_driver",
		return_value=frappe._dict(name=trip.driver, full_name="Rollback Fixture Driver"),
	):
		start_mobile_delivery_trip("rollback-fixture", trip.name)
	trip.reload()
	assert trip.vuna_actual_start
	receipt_name = frappe.db.get_value(
		"Transport Trip Task", {"delivery_trip": trip.name, "task_type": "Transfer Receipt"}, "name"
	)
	receipt = make_transfer_proof(receipt_name)
	# Evidence uploads need a real draft, but must not receive inventory or
	# finish the stop until the driver's final submission.
	from vuna_transport.api import prepare_mobile_transport_proof

	stock_count = frappe.db.count("Stock Entry")
	with patch(
		"vuna_transport.services.mobile.mobile_device_service._require_mobile_driver",
		return_value=frappe._dict(name=trip.driver),
	):
		draft = prepare_mobile_transport_proof("rollback-fixture", receipt_name, save_draft=True)
	assert draft["docstatus"] == 0 and draft["name"]
	assert frappe.db.count("Stock Entry") == stock_count
	receipt = frappe.get_doc("Proof of Delivery", draft["name"])
	quarantine = None
	quarantine_opening_qty = 0
	if depot_fraction and use_depot_fallback:
		demand = frappe.get_doc("Transport Demand", dispatch_task.transport_demand)
		quarantine = frappe.get_doc("Warehouse", demand.destination_warehouse)
		frappe.db.set_value("Warehouse", quarantine.name, "vuna_returns_warehouse", None)
		quarantine_opening_qty = (
			frappe.db.get_value(
				"Bin", {"warehouse": quarantine.name, "item_code": stock.items[0].item_code}, "actual_qty"
			)
			or 0
		)
	elif depot_fraction:
		demand = frappe.get_doc("Transport Demand", dispatch_task.transport_demand)
		source_warehouse = frappe.get_doc("Warehouse", stock.from_warehouse)
		quarantine = frappe.get_doc(
			{
				"doctype": "Warehouse",
				"warehouse_name": "Rollback Quarantine " + frappe.generate_hash(length=6),
				"company": stock.company,
				"parent_warehouse": source_warehouse.parent_warehouse,
				"warehouse_type": source_warehouse.warehouse_type,
			}
		).insert()
		frappe.db.set_value(
			"Warehouse", demand.destination_warehouse, "vuna_returns_warehouse", quarantine.name
		)
	receipt.received_by = "Rollback Fixture Receiver"
	receipt.remarks = "Partial receipt integration test; balance remains in transit"
	for row in receipt.items:
		row.delivered_qty = row.ordered_qty * accepted_fraction
		row.rejected_qty = row.ordered_qty - row.delivered_qty
		row.rejected_goods_handling = (
			"All remain on vehicle"
			if not depot_fraction
			else "All received by depot"
			if depot_fraction == 1
			else "Split between depot and vehicle"
		)
		# All-depot deliberately supplies no second quantity: the selector derives it.
		row.depot_received_rejected_qty = row.rejected_qty * depot_fraction if depot_fraction < 1 else 0
		row.rejection_reason_remarks = "Rejected packaging: rollback fixture"
	receipt.save()
	# This fixture item's policy requires outgoing QI. Supply genuine accepted
	# fixture inspections before auto-posting; production never fabricates QI.
	from vuna_transport.services.execution import transfer_proof_service

	make_receipt = transfer_proof_service._make_receipt_stock_entry

	def prepare_inspected_fixture(*args, **kwargs):
		prepared = make_receipt(*args, **kwargs)
		prepared.insert(ignore_permissions=True)
		_add_fixture_quality_inspections(prepared)
		return prepared

	with patch.object(
		transfer_proof_service, "_make_receipt_stock_entry", side_effect=prepare_inspected_fixture
	):
		receipt.submit()
	request_name = frappe.db.get_value(
		"Return Request", {"proof_of_delivery": receipt.name, "docstatus": ["!=", 2]}, "name"
	)
	assert request_name
	request = frappe.get_doc("Return Request", request_name)
	assert request.return_context == "Depot" and not request.customer
	assert request.source_warehouse == (
		stock.to_warehouse if not depot_fraction else quarantine.name if depot_fraction == 1 else None
	)
	assert len(request.items) == len(receipt.items) * (2 if 0 < depot_fraction < 1 else 1)
	assert all(row.requested_return_qty > 0 for row in request.items)
	request.submit()
	assert request.status == "Awaiting Receipt"
	assert receipt.outcome == ("Failed" if accepted_fraction == 0 else "Partially Completed")
	assert frappe.db.get_value("Transport Trip Task", receipt_name, "status") == "Completed with Variance"
	entry = make_receipt_stock_entry(receipt.name)
	assert entry.docstatus == 1
	assert make_receipt_stock_entry(receipt.name).name == entry.name
	assert frappe.db.get_value("Transport Trip Task", receipt_name, "status") == "Completed with Variance"
	stock.reload()
	posted_fraction = accepted_fraction + (1 - accepted_fraction) * depot_fraction
	assert all(abs(row.transferred_qty - row.transfer_qty * posted_fraction) < 0.00001 for row in stock.items)
	if depot_fraction:
		assert {row.t_warehouse for row in entry.items} == (
			{quarantine.name} if not accepted_fraction else {quarantine.name, demand.destination_warehouse}
		)
		assert (
			abs(
				frappe.db.get_value(
					"Bin", {"warehouse": quarantine.name, "item_code": stock.items[0].item_code}, "actual_qty"
				)
				- quarantine_opening_qty
				- sum(row.transfer_qty for row in entry.items if row.t_warehouse == quarantine.name)
			)
			< 0.00001
		)
	assert all(row.uom == stock.items[0].uom for row in entry.items)
	if verify_return_pickup:
		from vuna_transport.tests.depot_return_pickup_smoke import exercise

		return exercise(request, trip)
	if verify_cascade:
		from frappe.desk.form.linked_with import cancel_all_linked_docs, get_submitted_linked_docs

		ignored = [
			"Vehicle Allocation",
			"Transport Load",
			"Transport Trip Task",
			"Delivery Note",
			"Serial and Batch Bundle",
		]
		linked = get_submitted_linked_docs("Delivery Trip", trip.name, ignored)
		identities = {(row["doctype"], row["name"]) for row in linked["docs"]}
		assert ("Proof of Delivery", receipt.name) in identities
		assert ("Return Request", request.name) in identities
		assert ("Stock Entry", entry.name) in identities
		assert ("Stock Entry", stock.name) not in identities
		assert not any(doctype in ignored for doctype, _name in identities)
		cancel_all_linked_docs(frappe.as_json(linked["docs"]), ignored)
		trip.reload()
		trip.cancel()
		assert frappe.db.get_value("Proof of Delivery", receipt.name, "docstatus") == 2
		assert frappe.db.get_value("Stock Entry", entry.name, "docstatus") == 2
		assert frappe.db.get_value("Stock Entry", stock.name, "docstatus") == 1
		return {
			"frappe_downstream_cascade": "passed",
			"upstream_loading_stock_preserved": True,
			"rolled_back": True,
		}
	entry.cancel()
	assert frappe.db.get_value("Transport Trip Task", receipt_name, "status") == "Awaiting Stock Posting"
	stock.reload()
	assert all(not row.transferred_qty for row in stock.items)
	return {
		"depot_return_request_from_rejection": "passed",
		"mobile_trip_details": "passed",
		"destination_only_pod": "passed",
		"loading_pod_not_required": True,
		"partial_end_transit": "passed",
		"rejected_depot_fraction": depot_fraction,
		"requested_uom": entry.items[0].uom,
		"receipt_cancellation_reopens_task": True,
		"ledger_changes_rolled_back": True,
	}


def _add_fixture_quality_inspections(stock):
	"""Real accepted QI fixtures; do not disable the site's inspection policy."""
	for row in stock.items:
		qi = frappe.get_doc(
			{
				"doctype": "Quality Inspection",
				"inspection_type": "Outgoing",
				"reference_type": "Stock Entry",
				"reference_name": stock.name,
				"item_code": row.item_code,
				"sample_size": 1,
				"inspected_by": frappe.session.user,
				"status": "Accepted",
				"manual_inspection": 1,
			}
		).insert()
		qi.submit()
	stock.reload()
