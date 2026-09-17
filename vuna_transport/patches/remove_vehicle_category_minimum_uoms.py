"""Normalize minimum Vehicle Category capacities to the maximum-capacity UOMs."""

import frappe

from vuna_transport.services.shared.measurement_service import convert_measurement


def execute():
	"""Convert legacy minimum values before removing their duplicate UOM columns."""
	if not frappe.db.has_column("Vehicle Category", "minimum_payload_weight_uom"):
		return

	for row in frappe.get_all(
		"Vehicle Category",
		fields=[
			"name",
			"minimum_payload_weight",
			"minimum_payload_weight_uom",
			"max_payload_weight_uom",
			"minimum_volume",
			"minimum_volume_uom",
			"max_volume_uom",
		],
		limit_page_length=0,
	):
		values = {}
		if row.minimum_payload_weight and row.minimum_payload_weight_uom and row.max_payload_weight_uom:
			values["minimum_payload_weight"] = convert_measurement(
				row.minimum_payload_weight,
				row.minimum_payload_weight_uom,
				row.max_payload_weight_uom,
				"minimum payload weight",
			)
		if row.minimum_volume and row.minimum_volume_uom and row.max_volume_uom:
			values["minimum_volume"] = convert_measurement(
				row.minimum_volume,
				row.minimum_volume_uom,
				row.max_volume_uom,
				"minimum volume",
			)
		if values:
			frappe.db.set_value("Vehicle Category", row.name, values, update_modified=False)

	for fieldname in ("minimum_payload_weight_uom", "minimum_volume_uom"):
		if frappe.db.has_column("Vehicle Category", fieldname):
			frappe.db.sql_ddl(f"alter table `tabVehicle Category` drop column `{fieldname}`")
