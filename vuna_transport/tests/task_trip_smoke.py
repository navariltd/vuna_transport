"""Rollback-only planning smoke; never submit trips or notify real drivers."""

import frappe
from frappe.utils import now_datetime

from vuna_transport.services.demand.transport_demand_service import (
	build_source_legs,
	create_transport_demands,
)
from vuna_transport.services.integrations.erpnext_integration import create_delivery_trip_for_load
from vuna_transport.services.planning.transport_load_service import create_transport_load_from_demands


def run():
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
			load = frappe.get_doc(
				"Transport Load",
				create_transport_load_from_demands(
					[
						{
							"transport_demand": demand.name,
							"transport_demand_item": item.name,
							"allocated_quantity": item.transaction_qty,
						}
					]
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
			category = frappe.db.get_value("Vehicle", allocation.vehicle, "vuna_vehicle_category")
			load.required_vehicle_category = category
			load.save()
			load.db_set("active_vehicle_allocation", allocation.name)
			trip = create_delivery_trip_for_load(load.name)
			trip.insert()
			tasks = frappe.get_all(
				"Transport Trip Task",
				filters={"delivery_trip": trip.name},
				fields=["name", "task_type", "delivery_stop"],
			)
			assert {task.task_type for task in tasks} == {"Transfer Dispatch", "Transfer Receipt"}
			assert all(not stop.delivery_note for stop in trip.delivery_stops)
			trip.save()
			assert frappe.db.count("Transport Trip Task", {"delivery_trip": trip.name}) == 2
			frappe.delete_doc("Delivery Trip", trip.name)
			assert not frappe.db.exists("Transport Trip Task", {"delivery_trip": trip.name})
			return {
				"transfer_trip_without_delivery_notes": "passed",
				"tasks": 2,
				"idempotency_and_draft_cleanup": "passed",
				"rolled_back": True,
			}
		return {"result": "not exercised", "reason": "No eligible Material Transfer Request"}
	finally:
		frappe.db.rollback(save_point="task_trip_smoke")
