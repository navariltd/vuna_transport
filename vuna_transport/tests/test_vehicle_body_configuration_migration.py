"""Exercise the body migration against rows, including retry and existing vehicle values."""

import sqlite3
from unittest import TestCase
from unittest.mock import patch

import frappe

from vuna_transport.patches import migrate_vehicle_body_configuration as migration


class TestVehicleBodyConfigurationMigration(TestCase):
	def setUp(self):
		self.connection = sqlite3.connect(":memory:")
		self.addCleanup(self.connection.close)
		self.connection.row_factory = sqlite3.Row
		self.connection.execute(
			"""create table `tabVehicle Category` (
			name text, tanker integer, trailer integer, open_body integer, closed_body integer)"""
		)
		self.connection.execute(
			"create table tabVehicle (name text, vuna_vehicle_category text, vuna_body_configuration text)"
		)
		self.framework = self.enterContext(patch.object(migration, "frappe"))
		self.framework.db.table_exists.side_effect = lambda doctype: bool(
			self.connection.execute(
				"select name from sqlite_master where name=?", (f"tab{doctype}",)
			).fetchone()
		)
		self.framework.db.has_column.side_effect = self.has_column
		self.framework.db.sql.side_effect = self.sql

		self.add_column = self.enterContext(
			patch.object(migration, "add_column", side_effect=self.create_column)
		)

	def has_column(self, doctype, field):
		return any(
			row["name"] == field for row in self.connection.execute(f"pragma table_info(`tab{doctype}`)")
		)

	def create_column(self, doctype, field, fieldtype):
		self.connection.execute(f"alter table `tab{doctype}` add column `{field}` text")

	def sql(self, query, values=(), as_dict=False):
		rows = self.connection.execute(query.replace("%s", "?"), values).fetchall()
		return [frappe._dict(dict(row)) for row in rows] if as_dict else rows

	def add_vehicle(self, name, category, body=None):
		self.connection.execute("insert into tabVehicle values (?, ?, ?)", (name, category, body))

	def body(self, name):
		return self.connection.execute(
			"select vuna_body_configuration from tabVehicle where name=?", (name,)
		).fetchone()[0]

	def test_shared_category_backfills_each_blank_vehicle_without_overwriting_individual_selection(self):
		self.connection.execute("insert into `tabVehicle Category` values ('FRR', 0, 0, 1, 0)")
		self.add_vehicle("A", "FRR")
		self.add_vehicle("B", "FRR", "")
		self.add_vehicle("C", "FRR", "Closed Body")
		self.add_vehicle("D", "Other")
		migration.execute()
		migration.execute()  # Retrying cannot overwrite a vehicle's own choice.
		self.assertEqual(
			[self.body(name) for name in "ABCD"], ["Open Body", "Open Body", "Closed Body", None]
		)

	def test_conflicting_checkboxes_are_skipped_while_valid_categories_are_migrated(self):
		self.connection.execute("insert into `tabVehicle Category` values ('Valid', 1, 0, 0, 0)")
		self.connection.execute("insert into `tabVehicle Category` values ('Conflict', 0, 0, 1, 1)")
		self.add_vehicle("A", "Valid")
		self.add_vehicle("B", "Conflict")
		self.add_vehicle("C", "Conflict", "Trailer")
		migration.execute()
		self.assertEqual(self.body("A"), "Tanker")
		self.assertIsNone(self.body("B"))
		self.assertEqual(self.body("C"), "Trailer")
		self.framework.throw.assert_not_called()

	def test_invalid_category_dropdown_is_skipped_without_blocking_migration(self):
		self.create_column("Vehicle Category", "body_configuration", "Select")
		self.connection.execute("insert into `tabVehicle Category` values ('FRR', 0, 0, 1, 0, 'Unknown')")
		self.add_vehicle("A", "FRR")
		migration.execute()
		self.assertIsNone(self.body("A"))
		self.framework.throw.assert_not_called()

	def test_previously_migrated_dropdown_takes_precedence_over_retained_checkbox_values(self):
		self.create_column("Vehicle Category", "body_configuration", "Select")
		self.connection.execute("insert into `tabVehicle Category` values ('FRR', 0, 0, 1, 0, 'Closed Body')")
		self.add_vehicle("A", "FRR")
		migration.execute()
		self.assertEqual(self.body("A"), "Closed Body")

	def test_dropdown_only_installation_is_also_migrated(self):
		self.connection.execute("drop table `tabVehicle Category`")
		self.connection.execute("create table `tabVehicle Category` (name text, body_configuration text)")
		self.connection.execute("insert into `tabVehicle Category` values ('FRR', 'Trailer')")
		self.add_vehicle("A", "FRR")
		migration.execute()
		self.assertEqual(self.body("A"), "Trailer")

	def test_missing_vehicle_column_is_created_before_copy(self):
		self.connection.execute("drop table tabVehicle")
		self.connection.execute("create table tabVehicle (name text, vuna_vehicle_category text)")
		self.connection.execute("insert into tabVehicle values ('A', 'FRR')")
		self.connection.execute("insert into `tabVehicle Category` values ('FRR', 1, 0, 0, 0)")
		migration.execute()
		self.assertEqual(self.body("A"), "Tanker")
		self.add_column.assert_called_once_with("Vehicle", "vuna_body_configuration", "Select")

	def test_categories_without_body_selection_leave_vehicles_blank(self):
		self.connection.execute("insert into `tabVehicle Category` values ('FRR', 0, 0, 0, 0)")
		self.add_vehicle("A", "FRR")
		migration.execute()
		self.assertIsNone(self.body("A"))
