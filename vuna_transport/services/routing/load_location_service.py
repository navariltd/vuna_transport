"""Resolve actual cargo movement endpoints for shared routing and costing."""

import frappe

from vuna_transport.services.routing.location_service import (
	get_address_planning_data,
	get_warehouse_planning_data,
)


def get_load_planning_locations(load):
	"""Supplier origins are Addresses; internal origins/destinations are Warehouses.

	Keep the real direction of goods movement. In particular, do not reverse an
	inbound leg to make it match an outbound From Warehouse tariff.
	"""
	movement_type = getattr(load, "movement_type", None)
	if movement_type == "Supplier Pickup":
		origin = _address(getattr(load, "source_address", None))
	else:
		origin = _warehouse(getattr(load, "source_warehouse", None))
	destinations, seen = [], set()
	for row in getattr(load, "delivery_orders", []) or []:
		warehouse = getattr(row, "destination_warehouse", None)
		address = getattr(row, "delivery_address", None)
		key = (
			("Warehouse", warehouse)
			if movement_type in {"Supplier Pickup", "Warehouse Transfer"}
			else ("Address", address)
		)
		if key[1] and key in seen:
			continue
		seen.add(key)
		if movement_type in {"Supplier Pickup", "Warehouse Transfer"}:
			destination = _warehouse(warehouse)
		else:
			destination = _address(address)
			# Preserve customer location snapshots, including valid zero coordinates.
			for target, field in (
				("latitude", "destination_latitude"),
				("longitude", "destination_longitude"),
				("town", "town"),
				("constituency", "constituency"),
				("county", "county"),
				("territory", "territory"),
			):
				value = getattr(row, field, None)
				if value not in (None, ""):
					destination[target] = value
		destinations.append(destination)
	return origin, destinations


def _address(name):
	result = frappe._dict(get_address_planning_data(name) or {})
	result.update(name=name, cost_key=name)
	return result


def _warehouse(name):
	result = frappe._dict(get_warehouse_planning_data(name) or {})
	result.update(name=name, warehouse=name, cost_key=result.get("address") or name)
	return result
