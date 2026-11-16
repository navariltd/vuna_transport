"""Diversion starts with read-only, fail-closed source and custody checks."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import frappe

from vuna_transport.services.execution import shipment_diversion_service as service


class Document(SimpleNamespace):
	def get(self, key, default=None):
		return getattr(self, key, default)

	def set(self, key, value):
		setattr(self, key, value)

	def get_doc_before_save(self):
		return self.get("previous")

	def precision(self, field):
		return self.get("quantity_precision", 6)

	def append(self, field, values):
		getattr(self, field).append(Document(**values))

	def as_dict(self):
		return vars(self)


class TestShipmentDiversionContext(TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(service, "frappe"))

		def fail(message, *args, **kwargs):
			raise ValueError(message)

		self.framework.throw.side_effect = fail
		self.framework.db.exists.return_value = False
		self.enterContext(patch.object(service, "_", side_effect=lambda message: message))
		self.dispatcher = self.enterContext(patch.object(service, "_require_dispatcher"))
		self.permission = self.enterContext(patch.object(service, "require_document_permission"))
		self.policy = self.enterContext(
			patch.object(
				service, "get_source_policy", return_value=frappe._dict(required_workflow_state=None)
			)
		)
		self.warehouse = self.enterContext(
			patch.object(service, "resolve_vehicle_transit_warehouse", return_value="Vehicle Warehouse")
		)
		self.source = Document(
			name="DNI-1",
			item_code="MILK",
			uom="Carton",
			stock_uom="Pcs",
			conversion_factor=24,
			stock_qty=240,
			serial_and_batch_bundle="BUNDLE-1",
		)
		self.row = Document(
			name="RRI-1",
			delivery_note_item="DNI-1",
			item_code="MILK",
			uom="Carton",
			stock_uom="Pcs",
			conversion_factor=24,
			requested_return_qty=2,
			batch_no="B1",
		)
		self.proof_row = Document(
			name="PODI-1",
			delivery_note_item="DNI-1",
			item_code="MILK",
			uom="Carton",
			conversion_factor=24,
			rejected_qty=3,
			batch_no="B1",
			rejected_serial_no="",
			rejection_reason="Order Refused",
		)
		self.request = Document(
			name="RR-1",
			docstatus=1,
			status="Awaiting Sales Return",
			return_context="Customer",
			company="Company",
			customer="Customer A",
			delivery_note="DN-1",
			proof_of_delivery="POD-1",
			delivery_trip="TRIP-1",
			custody="Still With Vehicle",
			items=[self.row],
			custody_events=[frappe._dict(to_custody="Still With Vehicle")],
		)
		self.note = Document(
			name="DN-1",
			docstatus=1,
			is_return=0,
			company="Company",
			customer="Customer A",
			items=[self.source],
		)
		self.pod = Document(
			name="POD-1",
			docstatus=1,
			status="Partially Delivered",
			proof_type="Customer Delivery",
			delivery_note="DN-1",
			delivery_trip="TRIP-1",
			company="Company",
			customer="Customer A",
			return_request="RR-1",
			items=[self.proof_row],
		)
		self.trip = Document(
			name="TRIP-1",
			docstatus=1,
			status="In Transit",
			company="Company",
			vehicle="VEHICLE-1",
			driver="DRIVER-1",
		)
		self.docs = {
			("Return Request", "RR-1"): self.request,
			("Proof of Delivery", "POD-1"): self.pod,
			("Delivery Note", "DN-1"): self.note,
			("Delivery Trip", "TRIP-1"): self.trip,
		}
		self.framework.get_doc.side_effect = lambda doctype, name: self.docs[(doctype, name)]

	def context(self):
		return service.get_shipment_diversion_context("RR-1")

	def codes(self):
		return {row["code"] for row in self.context()["blockers"]}

	def stock_return(self, warehouse="Vehicle Warehouse", docstatus=1):
		self.request.return_stock_document = "SR-1"
		doc = Document(
			name="SR-1",
			docstatus=docstatus,
			is_return=1,
			return_against="DN-1",
			company="Company",
			customer="Customer A",
			vuna_return_request="RR-1",
			items=[Document(warehouse=warehouse)],
		)
		self.docs[("Delivery Note", "SR-1")] = doc
		return doc

	def test_read_only_preview_preserves_requested_uom_and_original_rejection_identity(self):
		result = self.context()
		self.assertTrue(result["can_plan_diversion"])
		self.assertTrue(result["preview_only"])
		self.assertFalse(result["execution_enabled"])
		self.assertFalse(result["stock_verified"])
		self.assertFalse(result["quantities_reserved"])
		self.assertEqual(result["customer_redelivery_clearance"], "Not assessed")
		item = result["items"][0]
		self.assertEqual(
			(item["candidate_quantity"], item["uom"], item["stock_qty"], item["stock_uom"]),
			(2, "Carton", 48, "Pcs"),
		)
		self.assertEqual(item["source_serial_and_batch_bundle"], "BUNDLE-1")
		self.assertEqual(item["proof_of_delivery_item"], "PODI-1")
		self.assertEqual(item["batch_no"], "B1")
		self.framework.new_doc.assert_not_called()
		self.framework.db.set_value.assert_not_called()
		self.framework.db.sql.assert_not_called()
		self.framework.db.commit.assert_not_called()

	def test_dispatcher_and_document_permissions_are_required(self):
		self.context()
		self.dispatcher.assert_called_once_with(self.request)
		self.assertEqual(
			[call.args for call in self.permission.call_args_list],
			[(self.pod, "read"), (self.note, "read"), (self.trip, "write")],
		)
		self.warehouse.assert_called_once_with(self.trip)

	def test_permission_denial_does_not_load_source_documents(self):
		self.dispatcher.side_effect = frappe.PermissionError
		with self.assertRaises(frappe.PermissionError):
			self.context()
		self.assertEqual(self.framework.get_doc.call_count, 1)

	def test_return_request_must_be_approved_and_active(self):
		for field, value in (
			("docstatus", 0),
			("docstatus", 2),
			("status", "Resolved"),
			("status", "Closed"),
		):
			previous = getattr(self.request, field)
			setattr(self.request, field, value)
			with (
				self.subTest(field=field, value=value),
				self.assertRaisesRegex(ValueError, "active, submitted"),
			):
				self.context()
			setattr(self.request, field, previous)

	def test_configured_workflow_approval_cannot_be_bypassed(self):
		self.policy.return_value.required_workflow_state = "Approved"
		with self.assertRaisesRegex(ValueError, "approval state"):
			self.context()
		self.request.workflow_state = "Approved"
		self.assertTrue(self.context()["can_plan_diversion"])

	def test_depot_returns_and_unlinked_manual_returns_are_outside_initial_scope(self):
		for field, value in (
			("return_context", "Depot"),
			("proof_of_delivery", None),
			("delivery_note", None),
		):
			previous = getattr(self.request, field)
			setattr(self.request, field, value)
			with self.subTest(field=field), self.assertRaisesRegex(ValueError, "customer delivery rejection"):
				self.context()
			setattr(self.request, field, previous)

	def test_source_lineage_is_verified(self):
		for field, value in (
			("docstatus", 0),
			("company", "Other"),
			("customer", "Other"),
			("delivery_trip", "Other"),
			("delivery_note", "Other"),
			("return_request", "Other"),
			("proof_type", "Transfer Receipt"),
			("status", "Accepted"),
		):
			previous = getattr(self.pod, field)
			setattr(self.pod, field, value)
			with self.subTest(field=field), self.assertRaisesRegex(ValueError, "same customer delivery"):
				self.context()
			setattr(self.pod, field, previous)

	def test_original_delivery_must_be_submitted_in_the_same_company(self):
		for field, value in (("docstatus", 0), ("is_return", 1), ("company", "Other"), ("customer", "Other")):
			previous = getattr(self.note, field)
			setattr(self.note, field, value)
			with self.subTest(field=field), self.assertRaises(ValueError):
				self.context()
			setattr(self.note, field, previous)
		self.trip.company = "Other"
		with self.assertRaisesRegex(ValueError, "trip must belong"):
			self.context()

	def test_last_rejected_stop_does_not_implicitly_reopen_a_completed_trip(self):
		self.trip.status = "Completed"
		result = self.context()
		self.assertFalse(result["can_plan_diversion"])
		self.assertIn("trip_not_active", {row["code"] for row in result["blockers"]})
		self.assertEqual(self.trip.status, "Completed")

	def test_missing_vehicle_or_driver_is_a_planning_blocker(self):
		self.trip.driver = None
		self.assertIn("missing_vehicle_or_driver", self.codes())
		self.trip.vehicle = None
		self.warehouse.reset_mock()
		self.assertIn("missing_vehicle_or_driver", self.codes())
		self.warehouse.assert_not_called()

	def test_rejection_does_not_implicitly_confirm_vehicle_custody(self):
		for custody in (None, "Unknown", "Left With Customer", "Returned To Depot", "Split Custody"):
			self.request.custody = custody
			with self.subTest(custody=custody):
				self.assertIn("custody_unconfirmed", self.codes())
		self.request.custody = "Still With Vehicle"
		self.request.custody_events = []
		self.assertIn("custody_unconfirmed", self.codes())

	def test_latest_custody_event_and_recorded_warehouse_must_agree(self):
		self.request.custody_events.append(frappe._dict(to_custody="Returned To Depot"))
		self.assertIn("custody_unconfirmed", self.codes())
		self.row.custody_warehouse = "Other Depot"
		self.assertIn("different_custody_location", self.codes())

	def test_existing_return_work_requires_review_before_another_movement(self):
		for doctype, code in (
			("Transport Trip Task", "existing_return_task"),
			("Return Inspection", "existing_inspection"),
		):
			self.framework.db.exists.side_effect = lambda dt, filters: dt == doctype
			self.assertIn(code, self.codes())

	def test_wrong_sales_return_receiving_warehouse_is_not_silently_rewritten(self):
		for status in (0, 1):
			doc = self.stock_return("Factory Warehouse", status)
			self.assertIn("sales_return_location_mismatch", self.codes())
			self.assertEqual(doc.items[0].warehouse, "Factory Warehouse")

	def test_matching_existing_vehicle_receipt_is_reused_as_context_not_posted_again(self):
		self.stock_return()
		result = self.context()
		self.assertEqual(result["sales_return"], "SR-1")
		self.assertTrue(result["can_plan_diversion"])
		self.assertFalse(result["stock_verified"])
		self.framework.new_doc.assert_not_called()

	def test_sales_return_must_match_original_customer_and_request(self):
		doc = self.stock_return()
		doc.vuna_return_request = "OTHER"
		with self.assertRaisesRegex(ValueError, "linked Sales Return"):
			self.context()

	def test_requested_quantity_cannot_exceed_rejected_or_dispatched_stock(self):
		for qty in (4, float("nan"), float("inf"), -1):
			self.row.requested_return_qty = qty
			with self.subTest(qty=qty), self.assertRaises(ValueError):
				self.context()
		self.row.requested_return_qty = 2
		self.source.stock_qty = 24
		with self.assertRaisesRegex(ValueError, "cannot exceed"):
			self.context()

	def test_item_uom_factor_and_batch_identity_cannot_be_changed(self):
		for field, value in (
			("item_code", "OTHER"),
			("uom", "Pcs"),
			("stock_uom", "Litre"),
			("conversion_factor", 0),
			("conversion_factor", 12),
			("batch_no", "B2"),
		):
			previous = getattr(self.row, field)
			setattr(self.row, field, value)
			with self.subTest(field=field), self.assertRaises(ValueError):
				self.context()
			setattr(self.row, field, previous)

	def test_duplicate_and_missing_source_rows_cannot_expand_candidate_quantity(self):
		self.request.items.append(self.row)
		with self.assertRaisesRegex(ValueError, "distinct"):
			self.context()
		self.request.items = [self.row]
		self.pod.items.append(self.proof_row)
		with self.assertRaisesRegex(ValueError, "repeated"):
			self.context()
		self.pod.items = []
		with self.assertRaisesRegex(ValueError, "distinct"):
			self.context()

	def test_legacy_stock_unit_pod_quantities_are_converted_once(self):
		self.proof_row.uom = None
		self.proof_row.rejected_qty = 48
		item = self.context()["items"][0]
		self.assertEqual((item["candidate_quantity"], item["stock_qty"]), (2, 48))

	def test_zero_requested_quantity_does_not_create_a_candidate(self):
		self.row.requested_return_qty = 0
		result = self.context()
		self.assertEqual(result["items"], [])
		self.assertFalse(result["can_plan_diversion"])

	def test_ambiguous_vehicle_warehouse_error_is_not_ignored(self):
		self.warehouse.side_effect = frappe.ValidationError("Configure vehicle warehouse")
		with self.assertRaises(frappe.ValidationError):
			self.context()


class TestShipmentDiversionPlans(TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(service, "frappe"))

		def fail(message, *args, **kwargs):
			raise ValueError(message)

		self.framework.throw.side_effect = fail
		self.framework.get_cached_value.return_value = 0
		self.framework.get_installed_apps.return_value = []
		self.enterContext(patch.object(service, "_", side_effect=lambda value: value))
		self.permission = self.enterContext(patch.object(service, "require_document_permission"))
		self.create_permission = self.enterContext(patch.object(service, "require_doctype_permission"))
		self.lock = self.enterContext(patch.object(service, "_lock_diversion_source"))
		self.uom = self.enterContext(patch.object(service, "validate_uom_is_integer"))
		self.candidate = {
			"return_request_item": "RRI-1",
			"proof_of_delivery_item": "PODI-1",
			"delivery_note_item": "DNI-1",
			"item_code": "MILK",
			"candidate_quantity": 2,
			"stock_qty": 48,
			"source_rejected_stock_qty": 72,
			"uom": "Carton",
			"stock_uom": "Pcs",
			"conversion_factor": 24,
			"batch_no": "B1",
			"rejected_serial_no": "",
			"source_serial_and_batch_bundle": "BUNDLE-1",
		}
		self.context = {
			"company": "Company",
			"customer": "Customer A",
			"sales_return": None,
			"proof_of_delivery": "POD-1",
			"delivery_note": "DN-1",
			"delivery_trip": "TRIP-1",
			"vehicle": "VEHICLE-1",
			"driver": "DRIVER-1",
			"vehicle_warehouse": "Vehicle Warehouse",
			"recorded_custody": None,
			"blockers": [{"code": "custody_unconfirmed", "message": "Confirm custody"}],
			"items": [self.candidate],
		}
		self.get_context = self.enterContext(
			patch.object(service, "_get_shipment_diversion_context", return_value=self.context)
		)
		self.read_reservations = service.diversion_reservations
		self.reservations = self.enterContext(
			patch.object(service, "diversion_reservations", return_value=[])
		)
		self.row = Document(return_request_item="RRI-1", qty=1, serial_no="")
		self.doc = Document(
			name="SD-1",
			docstatus=0,
			return_request="RR-1",
			reason="Original customer refused delivery",
			destination_type="Depot",
			customer=None,
			delivery_address=None,
			destination_warehouse="Depot",
			custody_confirmed=0,
			items=[self.row],
		)
		self.warehouse = Document(
			name="Depot", company="Company", is_group=0, disabled=0, warehouse_type="Stock"
		)
		self.framework.get_doc.return_value = self.warehouse

	def validate(self):
		service.validate_shipment_diversion(self.doc)

	def reserved(self, qty, request_item="RRI-1", proof_item="PODI-1", serial_no=""):
		return Document(
			return_request_item=request_item,
			proof_of_delivery_item=proof_item,
			stock_qty=qty,
			serial_no=serial_no,
		)

	def test_draft_uses_authoritative_identity_and_preserves_transaction_uom(self):
		self.row.item_code, self.row.stock_qty, self.doc.company = "Forged", 1, "Other"
		self.validate()
		self.assertEqual(
			(self.row.item_code, self.row.qty, self.row.uom, self.row.stock_qty), ("MILK", 1, "Carton", 24)
		)
		self.assertEqual(self.row.available_qty, 2)
		self.assertEqual(self.doc.company, "Company")
		self.assertEqual(self.doc.eligibility_notes, "Confirm custody")
		self.lock.assert_called_once_with("RR-1")
		self.reservations.assert_called_once_with("POD-1", "SD-1")
		self.assertEqual(self.uom.call_count, 2)
		self.framework.new_doc.assert_not_called()
		self.framework.db.set_value.assert_not_called()

	def test_customer_and_depot_drafts_cannot_reserve_the_same_quantity(self):
		self.reservations.return_value = [self.reserved(36)]
		with self.assertRaisesRegex(ValueError, "uncommitted"):
			self.validate()
		self.row.qty = 0.5
		self.validate()
		self.assertEqual(self.row.stock_qty, 12)

	def test_other_requests_for_same_pod_share_the_rejection_envelope(self):
		self.reservations.return_value = [self.reserved(60, request_item="OTHER-REQUEST-ITEM")]
		with self.assertRaisesRegex(ValueError, "uncommitted"):
			self.validate()
		self.row.qty = 0.5
		self.validate()

	def test_request_limit_and_pod_limit_are_both_applied(self):
		available, serials = service.available_diversion_stock(
			self.candidate, [self.reserved(24), self.reserved(36, request_item="OTHER")]
		)
		self.assertEqual(available, 12)
		self.assertEqual(serials, set())

	def test_edit_excludes_own_previous_reservation(self):
		self.validate()
		self.doc.previous = Document(
			**{
				field: self.doc.get(field)
				for field in (
					"return_request",
					"company",
					"proof_of_delivery",
					"delivery_note",
					"delivery_trip",
					"vehicle",
					"driver",
					"vehicle_warehouse",
				)
			}
		)
		self.row.qty = 2
		self.validate()
		self.assertEqual(self.row.stock_qty, 48)
		self.reservations.assert_called_with("POD-1", "SD-1")

	def test_invalid_quantities_and_duplicate_items_are_rejected(self):
		for qty in (0, -1, 3, float("nan"), float("inf")):
			self.row.qty = qty
			with self.subTest(qty=qty), self.assertRaises(ValueError):
				self.validate()
		self.row.qty = 1
		self.doc.items.append(self.row)
		with self.assertRaisesRegex(ValueError, "distinct"):
			self.validate()

	def test_qty_and_stock_precision_follow_the_field_settings(self):
		self.row.qty, self.row.quantity_precision = 0.125, 3
		self.validate()
		self.assertEqual(self.row.stock_qty, 3)
		self.row.qty, self.row.quantity_precision = 0.1254, 2
		self.validate()
		self.assertEqual((self.row.qty, self.row.stock_qty), (0.13, 3.12))

	def test_approval_requires_explicit_selected_goods_custody_confirmation(self):
		self.doc.docstatus = 1
		with self.assertRaises(ValueError):
			self.validate()
		self.doc.custody_confirmed = 1
		self.validate()
		self.assertEqual(self.doc.eligibility_notes, "")

	def test_confirmation_cannot_override_known_conflicting_custody(self):
		self.doc.custody_confirmed = 1
		for custody in (
			"Left With Customer",
			"Returned To Depot",
			"Split Custody",
			"Transferred To Another Vehicle",
		):
			self.context["recorded_custody"] = custody
			with self.subTest(custody=custody), self.assertRaisesRegex(ValueError, "elsewhere"):
				self.validate()

	def test_completed_trip_allows_draft_planning_but_not_approval(self):
		self.context["blockers"] = [{"code": "trip_not_active", "message": "Trip is complete"}]
		self.validate()
		self.doc.docstatus, self.doc.custody_confirmed = 1, 1
		with self.assertRaisesRegex(ValueError, "Trip is complete"):
			self.validate()

	def test_other_source_conflicts_block_even_draft_reservation(self):
		self.context["blockers"] = [{"code": "existing_return_task", "message": "Return already assigned"}]
		with self.assertRaisesRegex(ValueError, "already assigned"):
			self.validate()

	def test_existing_plan_cannot_change_source_or_vehicle_silently(self):
		self.validate()
		self.doc.previous = Document(
			**{
				field: self.doc.get(field)
				for field in (
					"return_request",
					"company",
					"proof_of_delivery",
					"delivery_note",
					"delivery_trip",
					"vehicle",
					"driver",
					"vehicle_warehouse",
				)
			}
		)
		self.context["vehicle"] = "NEW VEHICLE"
		with self.assertRaisesRegex(ValueError, "context has changed"):
			self.validate()
		self.doc.previous.return_request = "OTHER"
		with self.assertRaisesRegex(ValueError, "different Return Request"):
			self.validate()

	def test_customer_destination_requires_matching_address_and_different_customer(self):
		self.doc.destination_type, self.doc.destination_warehouse = "Customer", None
		self.doc.customer, self.doc.delivery_address = "Customer B", "ADDRESS-B"
		customer = Document(name="Customer B", disabled=0)
		address = Document(
			name="ADDRESS-B", disabled=0, links=[Document(link_doctype="Customer", link_name="Customer B")]
		)
		self.framework.get_doc.side_effect = (
			lambda doctype, name: customer if doctype == "Customer" else address
		)
		self.validate()
		address.links = []
		with self.assertRaisesRegex(ValueError, "must belong"):
			self.validate()
		customer.name = "Customer A"
		with self.assertRaisesRegex(ValueError, "different customer"):
			self.validate()

	def test_destination_must_be_chosen_before_approval(self):
		self.doc.destination_warehouse = None
		self.validate()
		self.doc.docstatus, self.doc.custody_confirmed = 1, 1
		with self.assertRaisesRegex(ValueError, "receiving depot"):
			self.validate()

	def test_depot_rejects_other_company_group_disabled_or_vehicle_warehouse(self):
		for field, value in (
			("company", "Other"),
			("is_group", 1),
			("disabled", 1),
			("warehouse_type", "Transit"),
		):
			old = getattr(self.warehouse, field)
			setattr(self.warehouse, field, value)
			with self.subTest(field=field), self.assertRaisesRegex(ValueError, "active, non-group"):
				self.validate()
			setattr(self.warehouse, field, old)

	def test_serialized_goods_cannot_share_already_reserved_serials(self):
		self.framework.get_cached_value.return_value = 1
		self.candidate.update(
			stock_qty=3,
			source_rejected_stock_qty=3,
			conversion_factor=1,
			uom="Pcs",
			rejected_serial_no="S1\nS2\nS3",
		)
		self.reservations.return_value = [self.reserved(1, serial_no="S1")]
		self.row.serial_no = "S1"
		with self.assertRaisesRegex(ValueError, "uncommitted rejected serial"):
			self.validate()
		self.row.serial_no = "S2"
		self.doc.docstatus, self.doc.custody_confirmed = 1, 1
		self.validate()
		self.assertEqual(self.row.stock_qty, 1)
		self.row.serial_no = ""
		with self.assertRaisesRegex(ValueError, "exact rejected serial"):
			self.validate()

	def test_make_opens_an_unsaved_plan_with_remaining_quantities_only(self):
		self.doc.items = []
		self.framework.new_doc.return_value = self.doc
		self.reservations.return_value = [self.reserved(24)]
		result = service.make_shipment_diversion("RR-1")
		self.assertEqual(result["items"][0].qty, 1)
		self.create_permission.assert_called_once_with("Shipment Diversion", "create")
		self.framework.db.set_value.assert_not_called()
		self.framework.db.commit.assert_not_called()

	def test_cancelled_diversions_do_not_hold_quantities_in_the_ledger(self):
		self.read_reservations("POD-1", "SD-1")
		query, values = self.framework.db.sql.call_args.args
		self.assertIn("d.docstatus in (0,1)", query)
		self.assertIn("for update", query)
		self.assertEqual(values, ("POD-1", "SD-1"))

	def test_approval_is_stamped_by_server_and_cancel_only_releases_the_plan(self):
		from unittest.mock import MagicMock

		from vuna_transport.vuna_transport.doctype.shipment_diversion.shipment_diversion import (
			ShipmentDiversion,
		)

		doc = Document(db_set=MagicMock())
		with patch(
			"vuna_transport.vuna_transport.doctype.shipment_diversion.shipment_diversion.now_datetime",
			return_value="NOW",
		):
			ShipmentDiversion.before_submit(doc)
		self.assertEqual(doc.approved_by, frappe.session.user)
		self.assertEqual(doc.approved_on, "NOW")
		ShipmentDiversion.on_cancel(doc)
		doc.db_set.assert_called_once_with("status", "Cancelled", update_modified=False)
		self.framework.new_doc.assert_not_called()
