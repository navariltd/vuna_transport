from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from vuna_transport.api import (
	get_delivery_loads_for_allocation,
	get_drivers_for_allocation,
	get_vehicles_for_allocation,
)


class TestVehicleFirstAllocationSearch(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.enterContext(patch("vuna_transport.api.now_datetime", return_value=datetime(2026, 11, 21, 8)))

	def test_vehicle_picker_checks_refrigeration_when_only_a_later_selected_load_requires_it(self):
		loads = [
			frappe._dict(name="AMBIENT", refrigeration_required=0),
			frappe._dict(name="COLD", refrigeration_required=1),
		]
		with (
			patch("vuna_transport.api.validate_allocation_load_set", return_value=loads),
			patch("vuna_transport.api.require_document_permission"),
			patch("vuna_transport.api.get_allocation_resource_window", return_value=("start", "end")),
			patch("vuna_transport.api.get_vehicle_availability_map", return_value={}) as availability,
		):
			get_vehicles_for_allocation("Vehicle", "", "name", 0, 20, {"delivery_loads": ["AMBIENT", "COLD"]})
		self.assertTrue(availability.call_args.kwargs["refrigeration_required"])

	def test_optional_category_keeps_availability_and_permission_filters(self):
		for category in (None, "Canter"):
			with (
				self.subTest(category=category),
				patch("vuna_transport.api.get_vehicle_availability_map") as availability,
				patch("vuna_transport.api.frappe.get_list") as get_list,
			):
				availability.return_value = {
					"TRUCK": frappe._dict(vehicle="TRUCK", dispatch_eligible=True),
					"UNAVAILABLE": frappe._dict(vehicle="UNAVAILABLE", dispatch_eligible=False),
				}
				get_list.return_value = [("TRUCK", "KDA 123A", "Make", "Model")]
				result = get_vehicles_for_allocation(
					"Vehicle",
					"KDA",
					"name",
					0,
					20,
					{"company": "Company", "required_vehicle_category": category},
				)
				self.assertEqual(result, get_list.return_value)
				self.assertEqual(availability.call_args.kwargs["vehicle_category"], category)
				self.assertEqual(availability.call_args.kwargs["company"], "Company")
				self.assertEqual(get_list.call_args.kwargs["filters"], {"name": ["in", ["TRUCK"]]})
				self.assertTrue(get_list.call_args.kwargs["as_list"])

	def test_missing_category_does_not_offer_unavailable_vehicles(self):
		with (
			patch("vuna_transport.api.get_vehicle_availability_map", return_value={}),
			patch("vuna_transport.api.frappe.get_list") as get_list,
		):
			self.assertEqual(
				get_vehicles_for_allocation("Vehicle", "", "name", 0, 20, {"company": "Company"}), []
			)
			get_list.assert_not_called()


class TestVehicleAllocationDriverSearch(UnitTestCase):
	@patch("vuna_transport.api.frappe.get_list")
	@patch("vuna_transport.api.get_driver_availability_map")
	@patch("vuna_transport.api.get_allocation_resource_window")
	def test_searches_currently_available_drivers_without_delivery_loads(
		self, get_resource_window, get_availability_map, get_list
	):
		def get_driver_rows(doctype, **kwargs):
			if doctype != "Driver":
				return []
			if kwargs.get("pluck") == "name":
				return ["DR-001"]
			return [("DR-001", "Jane Driver", "0700000000", "DL-001")]

		get_list.side_effect = get_driver_rows
		get_resource_window.return_value = ("2026-10-27 08:00:00", "2026-10-27 08:00:01")
		get_availability_map.return_value = {"DR-001": frappe._dict(driver="DR-001", dispatch_eligible=True)}

		result = get_drivers_for_allocation(
			"Driver", "", "name", 0, 20, {"company": "Yovuna Tech", "delivery_loads": []}
		)

		self.assertEqual(result, [("DR-001", "Jane Driver", "0700000000", "DL-001")])
		availability_arguments = get_availability_map.call_args.kwargs
		self.assertEqual(availability_arguments["company"], "Yovuna Tech")
		self.assertEqual(availability_arguments["driver_names"], ["DR-001"])


class TestAllocationLoadSearch(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.candidates = [
			frappe._dict(
				name=name, company="Company", required_vehicle_category="FH", active_vehicle_allocation=None
			)
			for name in ("BAD-UNITS", "TOO-HEAVY", "FITS")
		]
		self.enterContext(patch("vuna_transport.api.frappe.get_all", return_value=self.candidates))
		self.enterContext(
			patch("vuna_transport.api._loads_at_vehicle_allocation_step", side_effect=lambda rows: rows)
		)
		self.permission = self.enterContext(patch("vuna_transport.api.require_document_permission"))
		self.validation = self.enterContext(patch("vuna_transport.api.validate_allocation_load_set"))
		self.old_mute = frappe.flags.mute_messages
		self.old_messages = frappe.local.message_log
		frappe.flags.mute_messages = False
		frappe.local.message_log = []
		self.addCleanup(self.restore_messages)

	def restore_messages(self):
		frappe.flags.mute_messages = self.old_mute
		frappe.local.message_log = self.old_messages

	def search(self, **kwargs):
		return get_delivery_loads_for_allocation(
			"Transport Load",
			"",
			"name",
			0,
			20,
			{
				"company": "Company",
				"required_vehicle_category": "FH",
				"selected_delivery_loads": ["SELECTED"],
			},
			**kwargs,
		)

	def reject_incompatible(self, names, category):
		if names[-1] == "BAD-UNITS":
			frappe.throw("Selected Transport Loads must use the same transport Weight and Volume UOMs.")
		if names[-1] == "TOO-HEAVY":
			frappe.throw("Vehicle Category FH is incompatible: Weight capacity exceeded.")
		return [frappe._dict(name=name) for name in names]

	def test_search_returns_only_fitting_loads_without_popup_messages(self):
		self.validation.side_effect = self.reject_incompatible
		self.assertEqual(self.search(), [("FITS", "FITS")])
		self.assertEqual(frappe.local.message_log, [])
		self.assertFalse(frappe.flags.mute_messages)
		self.validation.assert_any_call(["SELECTED", "FITS"], "FH")

	def test_load_picker_excludes_cold_cargo_for_an_ordinary_vehicle(self):
		self.validation.side_effect = lambda names, category: [
			frappe._dict(name=names[-1], refrigeration_required=int(names[-1] == "FITS"))
		]
		for cold in (0, 1):
			with patch(
				"vuna_transport.api.frappe.db.get_value",
				return_value=frappe._dict(vuna_vehicle_category="FRR", vuna_refrigerated=cold),
			):
				rows = get_delivery_loads_for_allocation(
					"Transport Load", "", "name", 0, 20, {"vehicle": "TRUCK"}
				)
			self.assertEqual(
				[row[0] for row in rows], [row.name for row in self.candidates if cold or row.name != "FITS"]
			)
		self.assertEqual(frappe.local.message_log, [])

	def test_dictionary_search_also_silently_filters_rejected_candidates(self):
		self.validation.side_effect = self.reject_incompatible
		self.assertEqual([row["name"] for row in self.search(as_dict=True)], ["FITS"])
		self.assertEqual(frappe.local.message_log, [])

	def test_permission_denied_candidates_are_not_offered(self):
		self.validation.return_value = [frappe._dict(name="LOAD")]
		self.permission.side_effect = lambda *args: frappe.throw("No read permission", frappe.PermissionError)
		self.assertEqual(self.search(), [])
		self.assertEqual(frappe.local.message_log, [])
		self.assertFalse(frappe.flags.mute_messages)

	def test_unexpected_errors_propagate_and_restore_message_state(self):
		self.validation.side_effect = RuntimeError("Unexpected failure")
		with self.assertRaisesRegex(RuntimeError, "Unexpected failure"):
			self.search()
		self.assertFalse(frappe.flags.mute_messages)

	def test_existing_message_and_mute_state_are_preserved(self):
		frappe.flags.mute_messages = True
		frappe.local.message_log.append(frappe._dict(message="Existing message"))
		self.validation.side_effect = self.reject_incompatible
		self.search()
		self.assertTrue(frappe.flags.mute_messages)
		self.assertEqual([row.message for row in frappe.local.message_log], ["Existing message"])

	def test_actual_validation_still_raises_and_displays_errors_outside_search(self):
		self.validation.side_effect = self.reject_incompatible
		self.search()
		with self.assertRaises(frappe.ValidationError):
			self.reject_incompatible(["TOO-HEAVY"], "FH")
		self.assertEqual(len(frappe.local.message_log), 1)
