"""Validation and master-data synchronization for ERPNext Vehicles."""

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime

from vuna_transport.services.fleet.vehicle_availability_service import (
	get_vehicle_availability_map,
	get_vehicle_status,
)
from vuna_transport.services.fleet.vehicle_owner_service import normalize_vehicle_owner
from vuna_transport.services.shared.security_service import require_document_permission


def validate_vehicle(doc, method=None):
	"""Populate standard Vehicle fields from the selected Vuna Vehicle Model."""
	_validate_vehicle_asset(doc)
	if not getattr(doc, "vuna_vehicle_model", None):
		return

	vehicle_model = frappe.get_cached_doc("Vehicle Model", doc.vuna_vehicle_model)
	if vehicle_model.disabled:
		frappe.throw(_("Vehicle Model {0} is disabled.").format(vehicle_model.name))

	doc.make = vehicle_model.manufacturer
	doc.model = vehicle_model.model_name
	if vehicle_model.vehicle_category:
		doc.vuna_vehicle_category = vehicle_model.vehicle_category
	if vehicle_model.fuel_type and vehicle_model.fuel_type in (
		"Petrol",
		"Diesel",
		"Natural Gas",
		"Electric",
	):
		doc.fuel_type = vehicle_model.fuel_type


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_unlinked_assets(doctype, txt, searchfield, start, page_len, filters=None):
	"""Return submitted, usable Assets not already linked to another Vehicle."""
	filters = frappe.parse_json(filters or {})
	company = filters.get("company")
	current_vehicle = filters.get("current_vehicle") or ""
	searchfield = searchfield if searchfield in ("name", "asset_name") else "name"
	conditions = [
		"a.docstatus = 1",
		"a.status not in ('Sold', 'Scrapped', 'Cancelled')",
		"not exists (select 1 from `tabVehicle` v where v.vuna_asset = a.name and v.name != %s)",
	]
	values = [current_vehicle]
	if company:
		conditions.append("a.company = %s")
		values.append(company)
	if txt:
		conditions.append(f"a.`{searchfield}` like %s")
		values.append(f"%{txt}%")
	return frappe.db.sql(
		f"""
		select a.name, a.asset_name, a.company
		from `tabAsset` a
		where {" and ".join(conditions)}
		order by a.asset_name asc, a.name asc
		limit %s offset %s
		""",
		[*values, page_len, start],
	)


def _validate_vehicle_asset(doc):
	asset_name = getattr(doc, "vuna_asset", None)
	if not asset_name:
		if (
			getattr(doc, "vuna_transport_enabled", 0)
			and normalize_vehicle_owner(getattr(doc, "vuna_vehicle_owner", None)) == "Company"
		):
			frappe.throw(_("A company-owned transport Vehicle must have a linked Asset."))
		return
	asset = frappe.get_cached_doc("Asset", asset_name)
	if not doc.company and asset.company:
		doc.company = asset.company
	if doc.company and asset.company and doc.company != asset.company:
		frappe.throw(
			_("Vehicle company {0} must match Asset {1} company {2}.").format(
				doc.company, asset.name, asset.company
			)
		)
	duplicate = frappe.db.get_value(
		"Vehicle",
		{
			"name": ["!=", doc.name or ""],
			"vuna_asset": asset.name,
		},
		"name",
	)
	if duplicate:
		frappe.throw(_("Asset {0} is already linked to Vehicle {1}.").format(asset.name, duplicate))


@frappe.whitelist()
def get_vehicle_operational_status(vehicle):
	"""Return the current derived status for one readable standard Vehicle."""
	doc = frappe.get_doc("Vehicle", vehicle)
	require_document_permission(doc, "read")
	result = get_vehicle_status(doc.name)
	result.evaluated_at = now_datetime()
	return result


def sync_vehicle_operational_snapshots(vehicle_names=None):
	"""Refresh filterable Vehicle status snapshots using one bulk evaluation."""
	if not frappe.db.table_exists("Vehicle"):
		return
	meta = frappe.get_meta("Vehicle")
	if not meta.has_field("vuna_operational_status"):
		return

	vehicle_names = list(dict.fromkeys(vehicle_names or []))
	if not vehicle_names:
		vehicle_names = frappe.get_all("Vehicle", pluck="name")
	if not vehicle_names:
		return

	evaluated_at = now_datetime()
	results = get_vehicle_availability_map(
		vehicle_names=vehicle_names,
		from_time=evaluated_at,
		to_time=add_to_date(evaluated_at, seconds=1),
	)
	for vehicle_name, result in results.items():
		frappe.db.set_value(
			"Vehicle",
			vehicle_name,
			{
				"vuna_operational_status": result.operational_state,
				"vuna_dispatch_eligible": cint(result.dispatch_eligible),
				"vuna_status_updated_at": evaluated_at,
			},
			update_modified=False,
		)
	return results


def sync_related_vehicle_snapshots(doc, method=None):
	"""Resolve Vehicles affected by an authoritative operational record."""
	vehicle_names = set()
	doctype = doc.doctype
	if doctype == "Vehicle":
		vehicle_names.add(doc.name)
	elif doctype in (
		"Vehicle Document",
		"Vehicle Availability Block",
		"Vehicle Incident",
	):
		_add_name(vehicle_names, getattr(doc, "vehicle", None))
	elif doctype == "Vehicle Allocation":
		_add_name(vehicle_names, getattr(doc, "vehicle", None))
		if getattr(doc, "replacement_of", None):
			_add_name(
				vehicle_names,
				frappe.db.get_value("Vehicle Allocation", doc.replacement_of, "vehicle"),
			)
	elif doctype == "Delivery Trip":
		_add_name(vehicle_names, getattr(doc, "vuna_current_vehicle", None))
		_add_name(vehicle_names, getattr(doc, "vehicle", None))
		before = doc.get_doc_before_save()
		if before:
			_add_name(vehicle_names, getattr(before, "vuna_current_vehicle", None))
			_add_name(vehicle_names, getattr(before, "vehicle", None))
	elif doctype in ("Asset", "Asset Repair"):
		asset = doc.name if doctype == "Asset" else getattr(doc, "asset", None)
		vehicle_names.update(_vehicles_for_asset(asset))
	elif doctype == "Asset Maintenance Log":
		maintenance = getattr(doc, "asset_maintenance", None)
		asset = frappe.db.get_value("Asset Maintenance", maintenance, "asset_name")
		vehicle_names.update(_vehicles_for_asset(asset))
	elif doctype == "Delivery Load":
		vehicle_names.update(
			frappe.get_all(
				"Vehicle Allocation",
				filters={"delivery_load": doc.name, "vehicle": ["is", "set"]},
				pluck="vehicle",
			)
		)

	if vehicle_names:
		sync_vehicle_operational_snapshots(sorted(vehicle_names))


def _vehicles_for_asset(asset):
	if not asset:
		return []
	return frappe.get_all("Vehicle", filters={"vuna_asset": asset}, pluck="name")


def _add_name(names, value):
	if value:
		names.add(value)
