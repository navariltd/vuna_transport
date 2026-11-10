"""Movement-neutral locations, units and rate safety without posting stock/accounting."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import frappe
from frappe.exceptions import PermissionError, ValidationError

from vuna_transport.services.costing import transport_cost_estimation_service as costing
from vuna_transport.services.planning import dispatch_planning_service as planner
from vuna_transport.services.routing import distance_estimation_service as distance
from vuna_transport.services.routing import load_location_service as locations
from vuna_transport.vuna_transport.doctype.transport_load import transport_load as load_controller
from vuna_transport.vuna_transport.report.loads_awaiting_vehicle import (
	loads_awaiting_vehicle as pending_loads,
)


class TestMovementLocations(TestCase):
	def setUp(self):
		self.enterContext(patch.object(costing, "_", side_effect=lambda value: value))
		self.enterContext(patch.object(distance, "_", side_effect=lambda value: value))
		self.addresses = {
			"Supplier": frappe._dict(
				address="Supplier", latitude=0, longitude=0, territory="Supplier Region"
			),
			"Customer": frappe._dict(
				address="Customer", latitude=2, longitude=3, territory="Customer Region"
			),
		}
		self.warehouses = {
			"Factory": frappe._dict(
				address="Factory Address", latitude=0, longitude=1, territory="Factory Region"
			),
			"Depot": frappe._dict(address="Depot Address", latitude=0, longitude=2, territory="Depot Region"),
		}
		self.get_address = self.enterContext(
			patch.object(
				locations,
				"get_address_planning_data",
				side_effect=lambda name: self.addresses.get(name, frappe._dict()),
			)
		)
		self.get_warehouse = self.enterContext(
			patch.object(
				locations,
				"get_warehouse_planning_data",
				side_effect=lambda name: self.warehouses.get(name, frappe._dict()),
			)
		)
		self.vehicle = SimpleNamespace(name="Truck", uom="Kilometer", vuna_expected_fuel_efficiency=5)

	def load(self, movement="Supplier Pickup", **values):
		return SimpleNamespace(
			**{
				"company": "Company",
				"movement_type": movement,
				"source_address": "Supplier",
				"source_warehouse": None if movement == "Supplier Pickup" else "Factory",
				"dispatch_date": "2026-11-10",
				"estimated_distance": None,
				"delivery_orders": [SimpleNamespace(destination_warehouse="Depot", delivery_address=None)],
				**values,
			}
		)

	def context(self, load, uom="Kilometer"):
		return costing._build_context(
			load,
			self.vehicle,
			frappe._dict(distance_uom=uom),
			as_of_date=None,
			distance=None,
			distance_source=None,
		)

	def test_supplier_pickup_uses_external_origin_and_receiving_warehouse(self):
		origin, destinations = locations.get_load_planning_locations(self.load())
		self.assertEqual(origin.name, "Supplier")
		self.assertEqual(origin.latitude, 0)
		self.assertEqual(destinations[0].warehouse, "Depot")
		self.assertEqual(destinations[0].cost_key, "Depot Address")
		self.get_warehouse.assert_called_once_with("Depot")

	def test_load_validation_refreshes_supplier_and_internal_distances(self):
		for movement in ("Supplier Pickup", "Warehouse Transfer"):
			load = self.load(movement)
			load.get = lambda field: getattr(load, field, None)
			with (
				patch.object(load_controller, "_validate_source_warehouse_change"),
				patch(
					"vuna_transport.services.planning.customer_demand_load_service.is_customer_demand_load",
					return_value=False,
				),
				patch.object(load_controller, "is_demand_load", return_value=True),
				patch.object(load_controller, "validate_demand_allocations"),
				patch.object(load_controller, "validate_capacity"),
				patch.object(load_controller, "apply_route_defaults") as route,
				patch.object(load_controller, "apply_delivery_load_distance_estimate") as estimate,
			):
				load_controller.TransportLoad.validate(load)
			estimate.assert_called_once_with(load)
			route.assert_not_called()

	def test_supplier_distance_fuel_and_destination_rates_use_same_movement(self):
		context = self.context(self.load())
		self.assertGreater(context.distance, 200)
		self.assertIsNone(context.source_warehouse)
		self.assertEqual(context.destination, "Depot Address")
		self.assertEqual(context.destination_geographies[0].territory, "Depot Region")
		component = frappe._dict(
			cost_component="Fuel",
			calculation_basis="Per Fuel Quantity",
			rate=100,
			rate_uom="Litre",
			cost_nature="Variable",
		)
		self.assertAlmostEqual(
			costing.estimate_cost_component(component, context)["amount"], context.distance / 5 * 100
		)
		self.assertTrue(any("straight-line" in warning for warning in context.planning_warnings))

	def test_transfers_and_depot_returns_use_warehouse_endpoints(self):
		for source_document_type in ("Material Request", "Return Request"):
			load = self.load("Warehouse Transfer", source_document_type=source_document_type)
			origin, targets = locations.get_load_planning_locations(load)
			self.assertEqual((origin.warehouse, targets[0].warehouse), ("Factory", "Depot"))
			self.assertGreater(self.context(load).distance, 100)

	def test_customer_snapshots_preserve_zero_and_do_not_mutate_addresses(self):
		load = self.load(
			"Customer Delivery",
			delivery_orders=[
				SimpleNamespace(delivery_address="Customer", destination_latitude=0, destination_longitude=0)
			],
		)
		_origin, targets = locations.get_load_planning_locations(load)
		self.assertEqual((targets[0].latitude, targets[0].longitude), (0, 0))
		self.assertEqual(self.addresses["Customer"].latitude, 2)
		self.assertEqual(targets[0].cost_key, "Customer")

	def test_duplicate_item_destinations_count_as_one_stop(self):
		row = SimpleNamespace(destination_warehouse="Depot")
		_origin, targets = locations.get_load_planning_locations(self.load(delivery_orders=[row, row]))
		self.assertEqual(len(targets), 1)
		self.get_warehouse.assert_called_once_with("Depot")

	def test_multiple_receiving_warehouses_keep_distinct_endpoints(self):
		load = self.load(
			delivery_orders=[
				SimpleNamespace(destination_warehouse="Depot"),
				SimpleNamespace(destination_warehouse="Factory"),
			]
		)
		context = self.context(load)
		self.assertEqual(len(context.destinations), 2)
		self.assertIsNone(context.destination)

	def test_missing_supplier_origin_does_not_invent_a_company_origin(self):
		context = self.context(self.load(source_address=None))
		self.assertEqual(context.distance, 0)
		self.assertTrue(
			any("Origin location is unavailable" in warning for warning in context.planning_warnings)
		)

	def test_context_converts_estimated_kilometres_without_mutating_load(self):
		load = self.load()
		with patch.object(costing, "convert_measurement", return_value=123) as convert:
			context = self.context(load, "Mile")
		self.assertEqual((context.distance, context.distance_uom), (123, "Mile"))
		self.assertEqual(convert.call_args.args[1:3], ("Kilometer", "Mile"))
		self.assertIsNone(load.estimated_distance)

	def test_fuel_quantity_is_converted_from_litres_to_the_rate_uom(self):
		context = self.context(self.load(estimated_distance=100))
		component = frappe._dict(
			cost_component="Fuel",
			calculation_basis="Per Fuel Quantity",
			rate=10,
			rate_uom="Gallon",
			cost_nature="Variable",
		)
		with patch.object(costing, "convert_measurement", return_value=5) as convert:
			result = costing.estimate_cost_component(component, context)
		convert.assert_called_once_with(20, "Litre", "Gallon", "fuel quantity")
		self.assertEqual((result["quantity"], result["amount"], result["uom"]), (5, 50, "Gallon"))

	def test_load_estimate_converts_to_settings_uom_and_preserves_manual_values(self):
		load = self.load()
		with (
			patch.object(distance, "get_transport_uoms", return_value=frappe._dict(distance_uom="Mile")),
			patch.object(distance, "convert_measurement", return_value=123),
		):
			distance.apply_delivery_load_distance_estimate(load)
		self.assertEqual(load.estimated_distance, 123)
		self.assertEqual(load.distance_estimate_method, "Geographic Estimate")
		manual = self.load(estimated_distance=70, distance_estimate_method="Manual")
		with patch.object(distance, "estimate_delivery_load_distance") as estimate:
			distance.apply_delivery_load_distance_estimate(manual)
		estimate.assert_not_called()
		self.assertEqual(manual.estimated_distance, 70)

	def test_inbound_tariff_is_not_reversed_to_match_from_warehouse(self):
		context = self.context(self.load(estimated_distance=100))
		profile = SimpleNamespace(
			destination_rates=[frappe._dict(source_warehouse="Depot", territory="Depot Region", rate=999)]
		)
		self.assertEqual(costing._destination_rate(None, context, profile), 0)
		profile.destination_rates.append(frappe._dict(territory="Depot Region", rate=150))
		self.assertEqual(costing._destination_rate(None, context, profile), 150)

	def test_missing_rate_warns_but_explicit_zero_rate_is_valid(self):
		context = self.context(self.load(estimated_distance=100))
		component = frappe._dict(
			cost_component="Transport", calculation_basis="Destination Rate", cost_nature="Variable"
		)
		profile = SimpleNamespace(destination_rates=[])
		result = costing.estimate_cost_component(component, context, profile)
		self.assertEqual(result["amount"], 0)
		self.assertTrue(any("No matching Destination Rate" in warning for warning in result["warnings"]))
		profile.destination_rates = [frappe._dict(territory="Depot Region", rate=0)]
		self.assertEqual(costing.estimate_cost_component(component, context, profile)["warnings"], [])

	def test_unrelated_legacy_rate_does_not_match_missing_geography(self):
		context = self.context(self.load(estimated_distance=100))
		context.destination_geographies = [frappe._dict()]
		profile = SimpleNamespace(
			destination_rates=[frappe._dict(destination_key="Another Address", rate=999)]
		)
		self.assertEqual(costing._destination_rate(None, context, profile), 0)

	def test_supplier_vehicle_estimate_applies_existing_cost_components_end_to_end(self):
		load = self.load()
		profile = frappe._dict(
			name="Assignment",
			cost_structure="Structure",
			currency="KES",
			components=[
				frappe._dict(
					cost_component="Distance",
					calculation_basis="Per Distance",
					rate=10,
					rate_uom="Kilometer",
					cost_nature="Variable",
				),
				frappe._dict(
					cost_component="Fuel",
					calculation_basis="Per Fuel Quantity",
					rate=100,
					rate_uom="Litre",
					cost_nature="Variable",
				),
				frappe._dict(
					cost_component="Transport", calculation_basis="Destination Rate", cost_nature="Variable"
				),
			],
			destination_rates=[frappe._dict(territory="Depot Region", rate=150)],
		)
		with (
			patch.object(costing, "_resolve_document", side_effect=[load, self.vehicle]),
			patch.object(costing, "require_document_permission"),
			patch.object(costing, "require_company_access"),
			patch.object(costing, "get_transport_uoms", return_value=frappe._dict(distance_uom="Kilometer")),
			patch.object(costing, "get_applicable_cost_structure_assignment", return_value=profile),
		):
			result = costing.estimate_transport_cost("TL", "Truck")
		self.assertAlmostEqual(
			result["estimated_full_cost"], result["distance"] * 10 + result["distance"] / 5 * 100 + 150
		)
		self.assertEqual(len(result["components"]), 3)
		self.assertEqual(result["cost_structure_assignment"], "Assignment")


class TestCombinedMovementCosts(TestCase):
	def setUp(self):
		self.enterContext(patch.object(costing, "_", side_effect=lambda value: value))
		self.enterContext(patch.object(costing.frappe, "throw", side_effect=ValidationError))
		self.enterContext(
			patch.object(
				costing,
				"_resolve_document",
				side_effect=[
					SimpleNamespace(name="Supplier Load"),
					SimpleNamespace(name="Customer Load"),
					SimpleNamespace(name="Truck", vuna_vehicle_category="Canter"),
				],
			)
		)
		self.estimates = self.enterContext(patch.object(costing, "estimate_transport_cost"))

	def estimate(self, **values):
		return {
			"distance": 100,
			"distance_uom": "Kilometer",
			"currency": "KES",
			"components": [],
			"marginal_cost": 100,
			"fixed_cost": 500,
			"cost_structure": "Structure",
			"cost_structure_assignment": "Assignment",
			**values,
		}

	def test_mixed_cost_currencies_cannot_be_summed(self):
		self.estimates.side_effect = [self.estimate(), self.estimate(currency="USD")]
		with self.assertRaises(ValidationError):
			costing.estimate_transport_cost_for_loads(["TL1", "TL2"], "Truck")

	def test_mixed_distance_units_are_normalized(self):
		self.estimates.side_effect = [self.estimate(), self.estimate(distance=50, distance_uom="Mile")]
		with patch.object(costing, "convert_measurement", return_value=80.4672) as convert:
			result = costing.estimate_transport_cost_for_loads(["TL1", "TL2"], "Truck")
		self.assertAlmostEqual(result["distance"], 180.4672)
		convert.assert_called_once_with(50, "Mile", "Kilometer", "transport distance")
		self.assertEqual(result["fixed_cost"], 500)

	def test_shared_trip_flat_charges_are_explicit_without_silently_changing_tariffs(self):
		self.estimates.side_effect = [
			self.estimate(components=[{"basis": "Flat Per Trip", "amount": 100}]),
			self.estimate(components=[{"basis": "Flat Per Trip", "amount": 100}]),
		]
		result = costing.estimate_transport_cost_for_loads(["TL1", "TL2"], "Truck")
		self.assertEqual(result["marginal_cost"], 200)
		self.assertTrue(any("Review shared-trip charges" in warning for warning in result["warnings"]))


class TestCostAssignmentScope(TestCase):
	def test_restricted_assignment_cannot_fall_back_to_matching_category(self):
		vehicle = SimpleNamespace(
			name="Truck",
			vuna_transport_cost_structure="Structure",
			vuna_vehicle_category="Canter",
			vuna_supplier="Carrier",
		)
		for mismatch in (
			{"vehicle": "Other Truck"},
			{"supplier": "Other Carrier"},
			{"vehicle": "Truck", "vehicle_category": "Tanker"},
		):
			profile = frappe._dict(
				name="Restricted",
				company="Company",
				docstatus=1,
				disabled=0,
				valid_from="2026-01-01",
				valid_to=None,
				vehicle=None,
				supplier=None,
				vehicle_category="Canter",
			)
			profile.update(mismatch)
			with (
				patch.object(costing.frappe, "get_all", return_value=[profile]),
				patch.object(costing.frappe, "get_doc") as get_doc,
			):
				self.assertIsNone(
					costing.get_applicable_cost_structure_assignment(vehicle, "Company", "2026-11-10")
				)
			get_doc.assert_not_called()


class TestMovementCostPreview(TestCase):
	def setUp(self):
		self.frappe = self.enterContext(patch.object(planner, "frappe"))
		self.enterContext(patch.object(planner, "_", side_effect=lambda value: value))
		self.read = self.enterContext(patch.object(planner, "require_document_permission"))
		self.permission = self.enterContext(patch.object(planner, "require_doctype_permission"))
		self.company = self.enterContext(patch.object(planner, "require_company_access"))
		self.frappe.throw.side_effect = ValidationError
		self.load = SimpleNamespace(
			name="TL", company="Company", movement_type="Supplier Pickup", required_vehicle_category="Truck"
		)
		self.frappe.get_doc.return_value = self.load
		self.frappe.db.get_single_value.return_value = 1

	def test_saved_supplier_transfer_and_customer_loads_share_preview(self):
		for movement in ("Supplier Pickup", "Warehouse Transfer", "Customer Delivery"):
			self.load.movement_type = movement
			with patch.object(
				planner, "_cost_structure_estimates_for_load", return_value={"cost_structures": []}
			) as estimate:
				planner.get_transport_load_cost_estimates("TL")
				estimate.assert_called_once_with(self.load, "Truck")
		self.read.assert_called_with(self.load, "read")
		self.company.assert_called_with("Company")

	def test_disabled_costing_cannot_be_bypassed(self):
		self.frappe.db.get_single_value.return_value = 0
		with (
			patch.object(planner, "_cost_structure_estimates_for_load") as estimate,
			self.assertRaises(ValidationError),
		):
			planner.get_transport_load_cost_estimates("TL")
		estimate.assert_not_called()

	def test_permission_denial_stops_preview(self):
		self.permission.side_effect = PermissionError
		with self.assertRaises(PermissionError):
			planner.get_transport_load_cost_estimates("TL")
		self.frappe.get_doc.assert_not_called()

	def test_cross_company_denial_stops_preview(self):
		self.company.side_effect = PermissionError
		with (
			patch.object(planner, "_cost_structure_estimates_for_load") as estimate,
			self.assertRaises(PermissionError),
		):
			planner.get_transport_load_cost_estimates("TL")
		estimate.assert_not_called()

	def test_different_currencies_are_not_ranked_as_one_recommended_price(self):
		self.frappe.get_all.return_value = [
			frappe._dict(name=name, cost_structure_name=name, vehicle_owner="Company")
			for name in ("KES", "USD")
		]
		with patch.object(
			planner,
			"estimate_transport_cost_for_cost_structure",
			side_effect=[
				{
					"cost_structure_assignment": name,
					"currency": name,
					"estimated_full_cost": amount,
					"warnings": [],
				}
				for name, amount in (("KES", 1000), ("USD", 10))
			],
		):
			result = planner._cost_structure_estimates_for_load(self.load)
		self.assertTrue(result["warnings"])
		self.assertFalse(any(row.get("recommended") for row in result["cost_structures"]))

	def test_vehicle_ranking_groups_currencies_and_does_not_treat_missing_price_as_free(self):
		rows = [
			{"vehicle": "Unpriced", "marginal_cost": 0, "currency": None, "cost_structure_assignment": None},
			{"vehicle": "USD", "marginal_cost": 10, "currency": "USD", "cost_structure_assignment": "A"},
			{"vehicle": "KES", "marginal_cost": 1000, "currency": "KES", "cost_structure_assignment": "B"},
		]
		result = planner._rank_costed_vehicles(rows)
		self.assertEqual([row["vehicle"] for row in result], ["KES", "USD", "Unpriced"])
		self.assertTrue(all(row["warnings"] for row in result))


class TestMovementPendingLoadsReport(TestCase):
	def setUp(self):
		self.enterContext(patch.object(pending_loads, "_", side_effect=lambda value: value))

	def test_methods_that_bypass_allocation_are_not_reported_as_waiting(self):
		rows = [
			frappe._dict(name="Pickup", delivery_method="Planned"),
			frappe._dict(name="Other", delivery_method="No Allocation"),
			frappe._dict(name="Default", delivery_method=None),
		]
		with (
			patch.object(pending_loads.frappe, "get_list", return_value=rows),
			patch.object(
				pending_loads.frappe,
				"get_cached_value",
				side_effect=lambda _doctype, name, _field: name == "Planned",
			),
		):
			_columns, result = pending_loads.execute()
		self.assertEqual([row.name for row in result], ["Pickup", "Default"])

	def test_native_approved_unallocated_loads_are_included_with_supplier_origin(self):
		with (
			patch.object(pending_loads.frappe, "get_list", return_value=[]) as get_list,
			patch.object(pending_loads.frappe, "get_all") as get_all,
		):
			columns, _rows = pending_loads.execute(
				{"company": "Company", "movement_type": "Supplier Pickup", "source_address": "Supplier"}
			)
		conditions = get_list.call_args.kwargs["filters"]
		self.assertEqual(conditions["status"], ["in", ["Approved", "Awaiting Vehicle"]])
		self.assertEqual(conditions["active_vehicle_allocation"], ["is", "not set"])
		self.assertEqual(conditions["company"], "Company")
		self.assertEqual(conditions["movement_type"], "Supplier Pickup")
		self.assertEqual(conditions["source_address"], "Supplier")
		self.assertIn("source_address", get_list.call_args.kwargs["fields"])
		self.assertEqual(get_list.call_args.kwargs["limit"], 500)
		self.assertTrue(any(row["fieldname"] == "source_address" for row in columns))
		get_all.assert_not_called()
