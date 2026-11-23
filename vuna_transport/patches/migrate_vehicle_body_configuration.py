"""Move legacy category body selections onto their individual vehicles before sync."""

import frappe
from frappe.database.schema import add_column

BODY_OPTIONS = (
	("tanker", "Tanker"),
	("trailer", "Trailer"),
	("open_body", "Open Body"),
	("closed_body", "Closed Body"),
)


def execute():
	if not frappe.db.table_exists("Vehicle Category") or not frappe.db.table_exists("Vehicle"):
		return
	legacy_fields = [field for field, _ in BODY_OPTIONS if frappe.db.has_column("Vehicle Category", field)]
	has_dropdown = frappe.db.has_column("Vehicle Category", "body_configuration")
	if not legacy_fields and not has_dropdown:
		return  # Fresh install: the Vehicle custom field is created by customization sync.

	fields = ["name", *legacy_fields]
	if has_dropdown:
		fields.append("body_configuration")
	rows = frappe.db.sql(
		"select " + ", ".join(f"`{field}`" for field in fields) + " from `tabVehicle Category`",
		as_dict=True,
	)
	selections = {}
	for row in rows:
		# A previously migrated dropdown supersedes retained checkbox columns.
		selected = row.get("body_configuration")
		if selected:
			if selected in {label for _, label in BODY_OPTIONS}:
				selections[row.name] = selected
			continue
		checked = [label for field, label in BODY_OPTIONS if row.get(field)]
		if len(checked) == 1:
			selections[row.name] = checked[0]
		# Missing, invalid or ambiguous selections stay blank for users to resolve
		# on their next Vehicle save. They must not block migration of the site.

	# This patch runs before model/customization sync removes the category fields.
	# Create only the column here; custom/vehicle.json supplies the field metadata.
	if not frappe.db.has_column("Vehicle", "vuna_body_configuration"):
		add_column("Vehicle", "vuna_body_configuration", "Select")
	for category, selected in selections.items():
		frappe.db.sql(
			"""update `tabVehicle` set vuna_body_configuration = %s
			where vuna_vehicle_category = %s and coalesce(vuna_body_configuration, '') = ''""",
			(selected, category),
		)
	# Leave removed category columns intact as recovery data. Never overwrite a
	# vehicle's own selection or keep it fetched from its category after migration.
