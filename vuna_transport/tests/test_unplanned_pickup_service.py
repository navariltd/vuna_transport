import hashlib
import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.utils import flt

from vuna_transport.services.execution import unplanned_pickup_service as service


def record(**values):
	result = SimpleNamespace(**values)
	result.get = lambda key, default=None: getattr(result, key, default)
	return result


class TestUnplannedCollection(unittest.TestCase):
	def setUp(self):
		self.policy_check = self.enterContext(patch.object(service, "require_unplanned_pickups_enabled"))
		self.framework = self.enterContext(patch.object(service, "frappe"))
		self.framework.throw.side_effect = lambda message, *args: (_ for _ in ()).throw(ValueError(message))
		self.framework.get_precision.return_value = 6
		self.framework.db.get_value.return_value = 0
		self.framework.db.sql.return_value = []
		self.enterContext(patch.object(service, "_", side_effect=lambda text: text))
		self.enterContext(
			patch.object(
				service,
				"flt",
				side_effect=lambda value, precision=None: flt(
					value, precision, rounding_method="Banker's Rounding"
				),
			)
		)
		self.enterContext(patch.object(service, "now_datetime", return_value=datetime(2026, 11, 4, 12)))
		self.permission = self.enterContext(patch.object(service, "require_document_permission"))
		self.original_stop_validator = service._require_collection_stop
		self.stop_check = self.enterContext(
			patch.object(
				service,
				"_require_collection_stop",
				side_effect=lambda trip, stop: "STOP-1"
				if stop == "STOP-1"
				else self.original_stop_validator(trip, stop),
			)
		)
		self.trip = record(
			name="TRIP-1",
			company="Company",
			docstatus=1,
			status="In Transit",
			vuna_actual_start=datetime(2026, 11, 4, 8),
			vuna_actual_completion=None,
			driver="DRIVER-1",
			vehicle="VEHICLE-1",
		)
		self.item = record(
			name="MILK",
			item_name="Milk",
			stock_uom="Pcs",
			uoms=[record(uom="Carton")],
			disabled=0,
			is_stock_item=1,
			has_batch_no=0,
			has_serial_no=0,
		)
		self.framework.get_doc.side_effect = (
			lambda doctype, name: self.trip if doctype == "Delivery Trip" else self.item
		)
		self.conversion = self.enterContext(
			patch(
				"erpnext.stock.get_item_details.get_conversion_factor", return_value={"conversion_factor": 10}
			)
		)
		self.row = record(
			item_code="MILK",
			uom="Carton",
			delivered_qty=2,
			batch_no=None,
			serial_no="",
			collection_reference_row="SOURCE-ROW-1",
		)
		self.enterContext(
			patch("vuna_transport.services.execution.collection_reference_service.frappe", self.framework)
		)
		self.enterContext(
			patch(
				"vuna_transport.services.execution.collection_reference_service.reference_options",
				side_effect=lambda *args, **kwargs: {
					"party_type": "Depot",
					"customer": None,
					"location": "Depot",
					"items": [
						{
							"reference_row": "SOURCE-ROW-1",
							"name": "MILK",
							"uom": "Carton",
							"qty": 10,
							"conversion_factor": self.conversion.return_value["conversion_factor"],
							"uom_factors": {
								"Carton": self.conversion.return_value["conversion_factor"],
								"Pcs": 1,
								"Pack": 5,
							},
							"stock_qty": 100,
							"warehouse": "Depot",
							"batch_no": self.row.batch_no,
							"serial_no": self.row.serial_no,
						}
					],
				},
			)
		)
		self.proof = record(
			name="POD-1",
			company="Company",
			delivery_trip="TRIP-1",
			collection_stop="STOP-1",
			collection_reference_doctype="Stock Entry",
			collection_reference_name="SOURCE-1",
			items=[self.row],
			flags=frappe._dict(mobile_collection=True),
			collection_party_type="Depot",
			collection_location="Depot loading bay",
			notes="Unplanned damaged stock collection",
			received_by="Depot Clerk",
			delivery_datetime=None,
			return_request=None,
			collection_review_status=None,
		)

	def test_collection_preserves_uom_and_does_not_require_task_or_demand(self):
		service.validate_collection(self.proof)
		self.assertEqual((self.row.uom, self.row.conversion_factor, self.row.stock_qty), ("Carton", 10, 20))
		self.assertEqual(self.proof.collection_review_status, "Pending Review")
		self.assertEqual(
			(self.proof.collection_driver, self.proof.collection_vehicle), ("DRIVER-1", "VEHICLE-1")
		)
		self.framework.new_doc.assert_not_called()

	def test_completed_trip_cannot_collect(self):
		self.trip.status = "Completed"
		with self.assertRaisesRegex(ValueError, "completed"):
			service.validate_collection(self.proof)

	def test_disabled_setting_blocks_direct_desk_creation_or_submission(self):
		self.policy_check.side_effect = ValueError("Unplanned pickups are disabled")
		with self.assertRaisesRegex(ValueError, "disabled"):
			service.validate_collection(self.proof)
		self.framework.get_doc.assert_not_called()

	def test_disabled_setting_blocks_new_mobile_reports(self):
		self.policy_check.side_effect = ValueError("Unplanned pickups are disabled")
		with patch(
			"vuna_transport.services.mobile.mobile_trip_execution_service._lock_assigned_trip",
			return_value=(Mock(), self.trip),
		):
			with self.assertRaisesRegex(ValueError, "disabled"):
				service.report_collection(
					"device", "TRIP-1", {"request_id": "12345678-1234-1234-1234-123456789012"}
				)
		self.framework.new_doc.assert_not_called()

	def test_disabled_setting_blocks_new_reference_item_lookup(self):
		self.policy_check.side_effect = ValueError("Unplanned pickups are disabled")
		with patch(
			"vuna_transport.services.mobile.mobile_trip_execution_service._lock_assigned_trip",
			return_value=(Mock(), self.trip),
		):
			with self.assertRaisesRegex(ValueError, "disabled"):
				service.collection_catalog("device", "TRIP-1", "STOP-1", "Stock Entry", "SOURCE-1")

	def test_disabled_setup_remains_available_for_history_without_source_lookup(self):
		with (
			patch.object(service, "unplanned_pickups_enabled", return_value=False),
			patch(
				"vuna_transport.services.mobile.mobile_device_service._require_mobile_driver",
				return_value=record(name="DRIVER-1"),
			),
			patch(
				"vuna_transport.services.mobile.mobile_device_service._get_assigned_trip",
				return_value=self.trip,
			),
		):
			self.assertEqual(
				service.collection_setup("device", "TRIP-1", "STOP-1"),
				{"enabled": False, "reference_types": []},
			)

	def test_collection_requires_a_stop(self):
		self.proof.collection_stop = None
		with self.assertRaisesRegex(ValueError, "collection stop"):
			service.validate_collection(self.proof)

	def test_another_trips_stop_cannot_be_used(self):
		self.proof.collection_stop = "OTHER-STOP"
		with self.assertRaisesRegex(ValueError, "collection stop"):
			service.validate_collection(self.proof)
		self.framework.db.get_value.assert_called_with(
			"Delivery Stop", {"name": "OTHER-STOP", "parent": "TRIP-1", "parenttype": "Delivery Trip"}, "name"
		)

	def test_history_is_scoped_to_the_selected_stop(self):
		self.framework.get_all.return_value = []
		with (
			patch(
				"vuna_transport.services.mobile.mobile_device_service._require_mobile_driver",
				return_value=record(name="DRIVER-1"),
			),
			patch(
				"vuna_transport.services.mobile.mobile_device_service._get_assigned_trip",
				return_value=self.trip,
			),
		):
			self.assertEqual(service.collection_history("device", "TRIP-1", "STOP-1"), [])
		self.assertEqual(
			self.framework.get_all.call_args.kwargs["filters"],
			{
				"delivery_trip": "TRIP-1",
				"collection_stop": "STOP-1",
				"proof_type": service.PROOF_TYPE,
				"docstatus": 1,
			},
		)

	def test_unstarted_trip_cannot_collect(self):
		self.trip.vuna_actual_start = None
		with self.assertRaisesRegex(ValueError, "started"):
			service.validate_collection(self.proof)

	def test_cross_company_collection_rejected(self):
		self.proof.company = "Another Company"
		with self.assertRaisesRegex(ValueError, "company"):
			service.validate_collection(self.proof)

	def test_stock_and_task_references_cannot_be_injected(self):
		self.proof.dispatch_stock_entry = "STOCK-1"
		with self.assertRaisesRegex(ValueError, "planned stock"):
			service.validate_collection(self.proof)

	def test_driver_cannot_mark_evidence_reviewed(self):
		self.proof.collection_review_status = "Reviewed"
		with self.assertRaisesRegex(ValueError, "Only dispatch"):
			service.validate_collection(self.proof)

	def test_unknown_uom_is_rejected_before_item_conversion(self):
		self.row.uom = "Unknown Packaging"
		with self.assertRaisesRegex(ValueError, "referenced document"):
			service.validate_collection(self.proof)
		self.conversion.assert_not_called()

	def test_piece_collection_uses_stock_factor_not_carton_or_client_factor(self):
		self.row.uom, self.row.delivered_qty, self.row.conversion_factor = "Pcs", 5, 999
		service.validate_collection(self.proof)
		self.assertEqual(
			(self.row.uom, self.row.conversion_factor, self.row.stock_qty, self.row.ordered_qty),
			("Pcs", 1, 5, 100),
		)
		self.conversion.assert_not_called()

	def test_alternate_pack_collection_uses_configured_conversion(self):
		self.row.uom, self.row.delivered_qty = "Pack", 2
		service.validate_collection(self.proof)
		self.assertEqual((self.row.conversion_factor, self.row.stock_qty, self.row.ordered_qty), (5, 10, 20))

	def test_nonpositive_and_nonfinite_quantities_rejected(self):
		for qty in (-1, 0, float("inf"), float("nan")):
			with self.subTest(qty=qty):
				self.row.delivered_qty = qty
				with self.assertRaisesRegex(ValueError, "positive"):
					service.validate_collection(self.proof)

	def test_batch_required_and_must_belong_to_item(self):
		self.item.has_batch_no = 1
		with self.assertRaisesRegex(ValueError, "valid batch"):
			service.validate_collection(self.proof)
		self.row.batch_no = "BATCH-1"
		self.framework.db.get_value.side_effect = (
			lambda doctype, *args, **kwargs: "MILK" if doctype == "Batch" else 0
		)
		service.validate_collection(self.proof)
		self.assertEqual(self.row.batch_no, "BATCH-1")

	def test_duplicate_serials_across_rows_rejected(self):
		self.item.has_serial_no = 1
		self.conversion.return_value = {"conversion_factor": 1}
		self.row.delivered_qty, self.row.serial_no = 1, "SERIAL-1"
		self.proof.items.append(
			record(
				item_code="MILK",
				delivered_qty=1,
				uom="Carton",
				serial_no="SERIAL-1",
				batch_no=None,
				collection_reference_row="SOURCE-ROW-1",
			)
		)
		self.framework.db.get_value.side_effect = (
			lambda doctype, *args, **kwargs: record(item_code="MILK", batch_no=None)
			if doctype == "Serial No"
			else 0
		)
		with self.assertRaisesRegex(ValueError, "multiple collection rows"):
			service.validate_collection(self.proof)

	def test_whole_number_uom_rejects_fraction(self):
		self.row.delivered_qty = 0.5
		self.framework.db.get_value.return_value = 1
		with self.assertRaisesRegex(ValueError, "whole-number"):
			service.validate_collection(self.proof)

	def test_quantity_follows_configured_float_precision(self):
		self.framework.get_precision.return_value = 3
		self.row.delivered_qty = 1.123456
		service.validate_collection(self.proof)
		self.assertEqual((self.row.delivered_qty, self.row.stock_qty), (1.123, 11.23))

	def test_collection_time_cannot_be_backdated_before_trip(self):
		self.proof.delivery_datetime = datetime(2026, 11, 3)
		with self.assertRaisesRegex(ValueError, "trip start"):
			service.validate_collection(self.proof)

	def test_review_requires_dispatch_role_not_just_write_permission(self):
		self.framework.get_roles.return_value = ["Driver"]
		with self.assertRaisesRegex(ValueError, "Dispatch review"):
			service._require_dispatch_review(self.proof)

	def test_review_accounts_for_previous_collections(self):
		self.proof.items = [record(item_code="MILK", batch_no="BATCH", stock_qty=20)]
		request = record(
			name="RR-1",
			items=[record(item_code="MILK", batch_no="BATCH", requested_return_qty=2, conversion_factor=10)],
		)
		self.framework.db.sql.return_value = [record(item_code="MILK", batch_no="BATCH", qty=10)]
		with self.assertRaisesRegex(ValueError, "exceeds"):
			service._validate_request_items(self.proof, request)

	def test_customer_review_preserves_original_delivery_note_item_reference(self):
		self.proof.collection_party_type = "Customer"
		self.proof.items = [record(collection_reference_row="DIFFERENT-SALES-LINE", stock_qty=10)]
		request = record(
			name="RR-1",
			items=[record(delivery_note_item="SOURCE-ROW-1", requested_return_qty=2, conversion_factor=10)],
		)
		with self.assertRaisesRegex(ValueError, "exceeds"):
			service._validate_request_items(self.proof, request)

	def test_stock_link_cannot_be_removed(self):
		entry = record(
			vuna_unplanned_collection=None,
			get_doc_before_save=lambda: record(vuna_unplanned_collection="POD-1"),
		)
		with self.assertRaisesRegex(ValueError, "cannot be changed"):
			service.validate_collection_stock(entry)

	def test_ordinary_stock_transactions_are_untouched(self):
		entry = record(vuna_unplanned_collection=None, get_doc_before_save=lambda: None)
		service.validate_collection_stock(entry)
		self.framework.get_doc.assert_not_called()

	def test_duplicate_report_returns_same_proof_without_posting_anything(self):
		self.policy_check.side_effect = ValueError("Unplanned pickups are disabled")
		payload = {"request_id": "12345678-1234-1234-1234-123456789012"}
		digest = hashlib.sha256(
			json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
		).hexdigest()
		self.framework.session.user = "driver@example.com"
		self.framework.db.get_value.return_value = record(
			name="POD-1", owner="driver@example.com", delivery_trip="TRIP-1", payload_hash=digest, docstatus=1
		)
		with patch(
			"vuna_transport.services.mobile.mobile_trip_execution_service._lock_assigned_trip",
			return_value=(Mock(), self.trip),
		):
			result = service.report_collection("device", "TRIP-1", payload)
		self.assertEqual(result, {"name": "POD-1", "created": False})
		self.policy_check.assert_not_called()
		self.framework.new_doc.assert_not_called()

	def test_duplicate_request_id_with_changed_payload_rejected(self):
		self.framework.db.get_value.return_value = record(
			name="POD-1",
			owner="driver@example.com",
			delivery_trip="TRIP-1",
			payload_hash="different",
			docstatus=1,
		)
		self.framework.session.user = "driver@example.com"
		with patch(
			"vuna_transport.services.mobile.mobile_trip_execution_service._lock_assigned_trip",
			return_value=(Mock(), self.trip),
		):
			with self.assertRaisesRegex(ValueError, "already been used"):
				service.report_collection(
					"device", "TRIP-1", {"request_id": "12345678-1234-1234-1234-123456789012"}
				)

	def _stock_fixture(self):
		self.proof.docstatus = 1
		self.proof.proof_type = service.PROOF_TYPE
		self.proof.collection_review_status = "Reviewed"
		self.proof.return_request = "RR-1"
		self.row.name = "POD-ROW-1"
		self.row.conversion_factor = 10
		request = record(
			name="RR-1", company="Company", docstatus=1, return_context="Depot", return_warehouse="Holding"
		)
		source = record(name="RR-ROW-1", custody_warehouse="Depot")
		self.framework.get_doc.side_effect = (
			lambda doctype, name: self.proof if doctype == "Proof of Delivery" else request
		)
		self.framework.db.exists.return_value = False
		self.enterContext(
			patch.object(service, "_collection_receipt_rows", return_value=[(self.row, source)])
		)
		line = record(
			item_code="MILK",
			qty=2,
			uom="Carton",
			conversion_factor=10,
			s_warehouse="Depot",
			t_warehouse="Holding",
			batch_no=None,
			serial_no="",
			serial_and_batch_bundle=None,
			vuna_return_request_item=source.name,
			vuna_transport_proof_item=self.row.name,
		)
		return record(
			name="STOCK-1",
			company="Company",
			vuna_unplanned_collection=self.proof.name,
			vuna_return_request=request.name,
			purpose="Material Transfer",
			add_to_transit=0,
			outgoing_stock_entry=None,
			items=[line],
			get_doc_before_save=lambda: None,
		)

	def test_collection_stock_accepts_only_exact_authorized_rows(self):
		service.validate_collection_stock(self._stock_fixture())

	def test_stock_receipt_quantity_cannot_be_inflated(self):
		entry = self._stock_fixture()
		entry.items[0].qty = 3
		with self.assertRaisesRegex(ValueError, "preserve collected"):
			service.validate_collection_stock(entry)

	def test_stock_cannot_be_redirected_to_another_warehouse(self):
		entry = self._stock_fixture()
		entry.items[0].t_warehouse = "Other"
		with self.assertRaisesRegex(ValueError, "preserve collected"):
			service.validate_collection_stock(entry)

	def test_only_one_active_stock_receipt_per_collection(self):
		entry = self._stock_fixture()
		self.framework.db.exists.return_value = True
		with self.assertRaisesRegex(ValueError, "already has an active"):
			service.validate_collection_stock(entry)

	def test_stock_request_link_cannot_be_forged(self):
		entry = self._stock_fixture()
		self.proof.return_request = "ANOTHER-RR"
		with self.assertRaisesRegex(ValueError, "reviewed depot collection"):
			service.validate_collection_stock(entry)

	def test_existing_case_can_match_without_a_planned_task_or_original_proof_link(self):
		self.row.name = "NEW-COLLECTION-ROW"
		source = record(
			name="RRI-1",
			source_pod_item="OLD-REJECTION-ROW",
			item_code="MILK",
			batch_no=None,
			custody_warehouse="Depot",
		)
		request = record(items=[source], source_warehouse="Depot", return_warehouse="Holding")
		with patch.object(service, "_validate_warehouses"):
			self.assertEqual(service._collection_receipt_rows(self.proof, request), [(self.row, source)])

	def test_ambiguous_existing_case_rows_require_dispatch_resolution(self):
		self.row.name = "NEW-COLLECTION-ROW"
		source = record(name="RRI-1", source_pod_item="OLD-ROW", item_code="MILK", batch_no=None)
		with self.assertRaisesRegex(ValueError, "ambiguous"):
			service._collection_receipt_rows(self.proof, record(items=[source, source]))
