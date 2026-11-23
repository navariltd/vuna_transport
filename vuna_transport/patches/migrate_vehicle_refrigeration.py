"""Preserve category refrigeration on individual vehicles before model sync."""

import frappe
from frappe.database.schema import add_column


def execute():
	if not frappe.db.table_exists("Vehicle") or not frappe.db.table_exists("Vehicle Category"):
		return
	if not frappe.db.has_column("Vehicle Category", "refrigerated"):
		return
	if not frappe.db.has_column("Vehicle", "vuna_refrigerated"):
		# Nullable until backfill so retries can distinguish missing from an
		# explicit No. Customization sync will supply the checkbox default.
		add_column("Vehicle", "vuna_refrigerated", "Check")
	for row in frappe.db.sql("select name, refrigerated from `tabVehicle Category`", as_dict=True):
		frappe.db.sql(
			"""update `tabVehicle` set vuna_refrigerated = %s
			where vuna_vehicle_category = %s and vuna_refrigerated is null""",
			(1 if row.refrigerated else 0, row.name),
		)
	frappe.db.sql("update `tabVehicle` set vuna_refrigerated = 0 where vuna_refrigerated is null")
	# Retain the old category column for recovery. A vehicle's own Yes or No
	# always wins, including when this migration is retried.
