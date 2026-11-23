"""Preserve legacy refrigeration and enforce the physical vehicle's capability."""

import sqlite3
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import frappe

from vuna_transport.patches import migrate_vehicle_refrigeration as migration
from vuna_transport.services.fleet import vehicle_allocation_service as allocation_service
from vuna_transport.services.fleet import vehicle_availability_service as availability_service
from vuna_transport.services.fleet.vehicle_category_service import evaluate_vehicle_category


class TestVehicleRefrigerationMigration(TestCase):
	def setUp(self):
		self.connection = sqlite3.connect(":memory:")
		self.addCleanup(self.connection.close)
		self.connection.row_factory = sqlite3.Row
		self.connection.execute("create table `tabVehicle Category` (name text, refrigerated integer)")
		self.connection.executemany(
			"insert into `tabVehicle Category` values (?, ?)", [("Cold", 1), ("FRR", 0)]
		)
		self.connection.execute("create table tabVehicle (name text, vuna_vehicle_category text)")
		self.connection.executemany(
			"insert into tabVehicle values (?, ?)", [("A", "Cold"), ("B", "FRR"), ("C", None)]
		)
		self.framework = self.enterContext(patch.object(migration, "frappe"))
		self.framework.db.table_exists.return_value = True
		self.framework.db.has_column.side_effect = lambda doctype, field: any(
			row["name"] == field for row in self.connection.execute(f"pragma table_info(`tab{doctype}`)")
		)
		self.framework.db.sql.side_effect = self.sql
		self.add_column = self.enterContext(
			patch.object(migration, "add_column", side_effect=self.create_column)
		)

	def create_column(self, doctype, field, fieldtype):
		self.connection.execute(f"alter table `tab{doctype}` add column `{field}` integer")

	def sql(self, query, values=(), as_dict=False):
		rows = self.connection.execute(query.replace("%s", "?"), values).fetchall()
		return [frappe._dict(dict(row)) for row in rows] if as_dict else rows

	def values(self):
		return [
			row[0]
			for row in self.connection.execute("select vuna_refrigerated from tabVehicle order by name")
		]

	def test_existing_category_values_are_copied_with_uncategorized_vehicles_defaulting_to_no(self):
		migration.execute()
		self.assertEqual(self.values(), [1, 0, 0])
		self.add_column.assert_called_once_with("Vehicle", "vuna_refrigerated", "Check")

	def test_existing_vehicle_yes_and_no_are_authoritative_and_retries_do_not_reset_them(self):
		self.create_column("Vehicle", "vuna_refrigerated", "Check")
		self.connection.execute("update tabVehicle set vuna_refrigerated=0 where name='A'")
		self.connection.execute("update tabVehicle set vuna_refrigerated=1 where name='B'")
		migration.execute()
		migration.execute()
		self.assertEqual(self.values(), [0, 1, 0])
		self.add_column.assert_not_called()

	def test_fresh_install_without_legacy_column_is_a_noop(self):
		self.connection.execute("drop table `tabVehicle Category`")
		self.connection.execute("create table `tabVehicle Category` (name text)")
		migration.execute()
		self.add_column.assert_not_called()


class TestVehicleRefrigerationPlanning(TestCase):
	def test_category_capacity_can_be_planned_before_a_refrigerated_vehicle_is_selected(self):
		category = SimpleNamespace(tail_lift=0, capacities={"weight": 5000})
		self.assertTrue(
			evaluate_vehicle_category({"weight": 1000}, category, {"refrigeration_required": True})[
				"compatible"
			]
		)

	def test_allocation_checks_refrigeration_on_every_load_including_later_loads(self):
		for required in (0, 1):
			loads = [
				SimpleNamespace(refrigeration_required=0),
				SimpleNamespace(refrigeration_required=required),
			]
			with (
				patch.object(
					allocation_service, "get_allocation_resource_window", return_value=("start", "end")
				),
				patch.object(allocation_service, "evaluate_vehicle_availability") as evaluate,
			):
				evaluate.return_value = SimpleNamespace(dispatch_eligible=True)
				allocation_service._validate_vehicle_is_available(
					SimpleNamespace(vehicle="A", name="VA-1"), loads
				)
				self.assertEqual(evaluate.call_args.kwargs["refrigeration_required"], bool(required))

	def test_load_aware_bulk_availability_filters_physical_vehicles_in_same_category(self):
		vehicles = [
			frappe._dict(
				name=name,
				license_plate=name,
				vuna_vehicle_category="FRR",
				vuna_refrigerated=cold,
				vuna_vehicle_owner="Supplier",
				vuna_transport_enabled=1,
				vuna_asset=None,
			)
			for name, cold in (("A", 0), ("B", 1))
		]
		with (
			patch.object(
				availability_service.frappe,
				"get_all",
				side_effect=lambda doctype, **kw: vehicles if doctype == "Vehicle" else [],
			),
			patch.object(availability_service.frappe.db, "table_exists", return_value=False),
			patch.object(availability_service, "get_vehicle_compliance_map", return_value={}),
			patch.object(availability_service, "_get_compatible_categories", return_value={"FRR"}),
		):
			result = availability_service.get_vehicle_availability_map(
				from_time="2026-11-23 08:00:00",
				to_time="2026-11-23 12:00:00",
				delivery_load=SimpleNamespace(refrigeration_required=1),
			)
			summary = availability_service.get_vehicle_category_availability(
				None, "2026-11-23 08:00:00", "2026-11-23 12:00:00", refrigeration_required=True
			)
		self.assertFalse(result["A"].dispatch_eligible)
		self.assertTrue(result["B"].dispatch_eligible)
		self.assertEqual(summary["categories"]["FRR"]["available"], 1)
		self.assertEqual(summary["categories"]["FRR"]["unavailable"], 1)
