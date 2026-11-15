"""Distinct draft and submitted quality evidence for return result groups."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from vuna_transport.services.execution import return_inspection_quality_service as service


class TestReturnInspectionQuality(TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(service, "frappe"))

		def fail(message, *args, **kwargs):
			raise ValueError(message)

		self.framework.throw.side_effect = fail
		self.framework.session.user = "inspector@example.com"
		self.framework.db.get_single_value.return_value = 0
		self.framework.db.exists.return_value = False
		self.framework.parse_json.side_effect = frappe.parse_json
		self.permission = self.enterContext(patch.object(service, "require_document_permission"))
		self.create_permission = self.enterContext(patch.object(service, "require_doctype_permission"))
		self.enterContext(patch.object(service, "_", side_effect=lambda message: message))
		self.doc = frappe._dict(
			name="RIN-1",
			docstatus=0,
			inspection_required=1,
			company="Company",
			sales_return="RETURN-1",
			receiving_stock_entry=None,
			results=[self.result("GROUP-1"), self.result("GROUP-2", "Rejected")],
		)
		self.qi = frappe._dict(
			name="QI-1",
			docstatus=0,
			company="Company",
			reference_type="Delivery Note",
			reference_name="RETURN-1",
			child_row_reference="DNI-1",
			item_code="MILK",
			batch_no="BATCH-1",
			sample_size=1,
			status="Accepted",
		)
		self.framework.get_doc.return_value = self.qi

	def result(self, name, result="Accepted"):
		return frappe._dict(
			name=name,
			idx=1,
			item_code="MILK",
			delivery_note_item="DNI-1",
			stock_entry_detail="SED-1",
			inspection_qty=10,
			result=result,
			batch_no="BATCH-1",
			serial_no="",
			quality_inspection=None,
		)

	def test_explicit_creation_generates_separate_drafts_without_duplicates(self):
		first, second = MagicMock(), MagicMock()
		first.name, second.name = "QI-1", "QI-2"
		self.framework.new_doc.side_effect = [first, second]
		service.create_missing_quality_inspections(self.doc)
		self.assertEqual([row.quality_inspection for row in self.doc.results], ["QI-1", "QI-2"])
		for qi in (first, second):
			values = qi.update.call_args.args[0]
			self.assertEqual(values["inspection_type"], "Incoming")
			self.assertEqual(values["reference_name"], "RETURN-1")
			self.assertEqual(values["child_row_reference"], "DNI-1")
			self.assertEqual(values["batch_no"], "BATCH-1")
			self.assertNotIn("status", values)  # QI findings determine the outcome, not the result row.
			qi.insert.assert_called_once_with()  # No permission bypass or auto-submit.
			qi.submit.assert_not_called()
		service.create_missing_quality_inspections(self.doc)
		self.assertEqual(self.framework.new_doc.call_count, 2)
		self.assertEqual(self.framework.db.set_value.call_count, 2)

	def test_depot_drafts_reference_exact_receiving_stock_entry_row(self):
		self.doc.receiving_stock_entry = "STE-1"
		self.doc.results = [self.doc.results[0]]
		service.create_missing_quality_inspections(self.doc)
		values = self.framework.new_doc.return_value.update.call_args.args[0]
		self.assertEqual(values["reference_type"], "Stock Entry")
		self.assertEqual(values["reference_name"], "STE-1")
		self.assertEqual(values["child_row_reference"], "SED-1")

	def test_finalized_inspections_do_not_create_quality_documents(self):
		for status in (1, 2):
			self.doc.docstatus = status
			service.create_missing_quality_inspections(self.doc)
		self.framework.new_doc.assert_not_called()

	def test_only_selected_rows_are_created_even_when_quality_is_optional(self):
		self.doc.inspection_required = 0
		service.create_missing_quality_inspections(self.doc, {"GROUP-2"})
		self.assertIsNone(self.doc.results[0].quality_inspection)
		self.assertIsNotNone(self.doc.results[1].quality_inspection)
		self.framework.new_doc.assert_called_once_with("Quality Inspection")

	def test_save_controller_has_no_automatic_quality_creation_hook(self):
		from vuna_transport.vuna_transport.doctype.return_inspection.return_inspection import ReturnInspection

		self.assertNotIn("on_update", ReturnInspection.__dict__)

	def action_doc(self):
		self.doc.modified = "2026-11-15 16:00:00.000001"
		self.doc.save = MagicMock()
		self.framework.get_doc.return_value = self.doc
		return self.doc.modified

	def test_create_action_checks_permissions_locks_parent_and_saves_links(self):
		modified = self.action_doc()
		with patch.object(service, "create_missing_quality_inspections", return_value=["QI-1"]) as create:
			result = service.create_quality_inspections("RIN-1", ["GROUP-1"], modified)
			create.assert_called_once_with(self.doc, {"GROUP-1"})
			self.assertEqual(result["created"], ["QI-1"])
			self.doc.save.assert_called_once_with()
		self.framework.get_doc.assert_called_once_with("Return Inspection", "RIN-1", for_update=True)
		self.permission.assert_called_once_with(self.doc, "write")
		self.create_permission.assert_called_once_with("Quality Inspection", "create")

	def test_action_rejects_stale_and_finalized_documents(self):
		modified = self.action_doc()
		with self.assertRaisesRegex(ValueError, "changed"):
			service.create_quality_inspections("RIN-1", ["GROUP-1"], "2026-11-14 16:00:00")
		for status in (1, 2):
			self.doc.docstatus = status
			with self.assertRaisesRegex(ValueError, "draft"):
				service.create_quality_inspections("RIN-1", ["GROUP-1"], modified)
		self.framework.new_doc.assert_not_called()

	def test_action_rejects_foreign_duplicate_and_invalid_selections(self):
		modified = self.action_doc()
		for rows in ([], "{}", [1], ["OTHER"], ["GROUP-1", "GROUP-1"], ["GROUP-1"] * 501):
			with (
				self.subTest(rows=rows[:2] if isinstance(rows, list) else rows),
				self.assertRaises(ValueError),
			):
				service.create_quality_inspections("RIN-1", rows, modified)
		self.framework.new_doc.assert_not_called()

	def test_action_does_not_bypass_quality_create_permission(self):
		self.create_permission.side_effect = frappe.PermissionError
		with self.assertRaises(frappe.PermissionError):
			service.create_quality_inspections("RIN-1", ["GROUP-1"], "2026-11-15")
		self.framework.get_doc.assert_not_called()

	def test_repeated_action_with_existing_links_does_not_create_or_save(self):
		modified = self.action_doc()
		self.doc.results[0].quality_inspection = "EXISTING"
		result = service.create_quality_inspections("RIN-1", ["GROUP-1"], modified)
		self.assertEqual(result["created"], [])
		self.framework.new_doc.assert_not_called()
		self.doc.save.assert_not_called()

	def test_manual_links_are_not_replaced(self):
		for row in self.doc.results:
			row.quality_inspection = "EXISTING-" + row.name
		service.create_missing_quality_inspections(self.doc)
		self.framework.new_doc.assert_not_called()

	def test_permission_failure_does_not_link_an_unsaved_qi(self):
		self.framework.new_doc.return_value.insert.side_effect = frappe.PermissionError
		with self.assertRaises(frappe.PermissionError):
			service.create_missing_quality_inspections(self.doc)
		self.framework.db.set_value.assert_not_called()
		self.assertIsNone(self.doc.results[0].quality_inspection)

	def test_draft_can_save_missing_or_unsubmitted_quality_evidence(self):
		row = self.doc.results[0]
		service.validate_result_quality_inspection(self.doc, row, set())
		row.quality_inspection = "QI-1"
		service.validate_result_quality_inspection(self.doc, row, set())
		self.permission.assert_called_once_with(self.qi, "read")

	def test_submission_requires_missing_and_draft_qis(self):
		self.doc.docstatus = 1
		row = self.doc.results[0]
		with self.assertRaisesRegex(ValueError, "required"):
			service.validate_result_quality_inspection(self.doc, row, set())
		row.quality_inspection = "QI-1"
		with self.assertRaisesRegex(ValueError, "must be submitted"):
			service.validate_result_quality_inspection(self.doc, row, set())

	def test_submitted_qis_must_match_each_accepted_and_rejected_group(self):
		self.doc.docstatus, self.qi.docstatus = 1, 1
		for outcome in ("Accepted", "Rejected"):
			row = self.doc.results[0]
			row.result, row.quality_inspection, self.qi.status = outcome, "QI-1", outcome
			service.validate_result_quality_inspection(self.doc, row, set())
			self.qi.status = "Rejected" if outcome == "Accepted" else "Accepted"
			with self.assertRaisesRegex(ValueError, "outcome"):
				service.validate_result_quality_inspection(self.doc, row, set())

	def test_same_qi_cannot_support_two_result_groups(self):
		for row in self.doc.results:
			row.quality_inspection = "QI-1"
		used = set()
		service.validate_result_quality_inspection(self.doc, self.doc.results[0], used)
		with self.assertRaisesRegex(ValueError, "own Quality Inspection"):
			service.validate_result_quality_inspection(self.doc, self.doc.results[1], used)

	def test_cancelled_or_different_item_source_row_and_batch_are_rejected(self):
		row = self.doc.results[0]
		row.quality_inspection = "QI-1"
		for field, value in (
			("docstatus", 2),
			("item_code", "OTHER"),
			("reference_name", "OTHER"),
			("reference_type", "Stock Entry"),
			("child_row_reference", "OTHER"),
			("batch_no", "OTHER"),
		):
			previous = self.qi[field]
			self.qi[field] = value
			with self.subTest(field=field), self.assertRaisesRegex(ValueError, "must match"):
				service.validate_result_quality_inspection(self.doc, row, set())
			self.qi[field] = previous

	def test_depot_evidence_uses_the_same_submission_rules(self):
		self.doc.receiving_stock_entry = "STE-1"
		self.doc.docstatus, self.qi.docstatus = 1, 1
		self.qi.reference_type, self.qi.reference_name = "Stock Entry", "STE-1"
		self.qi.child_row_reference = "SED-1"
		row = self.doc.results[0]
		row.quality_inspection = "QI-1"
		service.validate_result_quality_inspection(self.doc, row, set())

	def test_sample_size_cannot_exceed_split_quantity(self):
		self.doc.docstatus, self.qi.docstatus, self.qi.sample_size = 1, 1, 11
		self.doc.results[0].quality_inspection = "QI-1"
		with self.assertRaisesRegex(ValueError, "sample size"):
			service.validate_result_quality_inspection(self.doc, self.doc.results[0], set())

	def test_qi_cannot_be_reused_by_another_active_return_inspection(self):
		self.doc.results[0].quality_inspection = "QI-1"
		self.framework.db.exists.return_value = True
		with self.assertRaisesRegex(ValueError, "already linked"):
			service.validate_result_quality_inspection(self.doc, self.doc.results[0], set())

	def test_explicit_checkbox_overrides_the_default_setting(self):
		self.framework.db.get_single_value.return_value = 1
		self.doc.inspection_required = 0
		self.assertFalse(service.quality_inspection_required(self.doc))


class TestQualityInspectionIsolation(TestCase):
	def test_row_qis_do_not_overwrite_standard_receipt_backlinks(self):
		from vuna_transport.overrides.quality_inspection import ReturnQualityInspectionMixin

		with patch("vuna_transport.overrides.quality_inspection.frappe.db.exists", return_value=True):
			qi = SimpleNamespace(name="QI-1", flags={})
			ReturnQualityInspectionMixin.update_qc_reference(qi)

	def test_submitted_customer_and_depot_evidence_cannot_be_removed(self):
		from vuna_transport.services.execution.return_v2_service import (
			prepare_return_quality_inspection_removal,
		)

		with (
			patch("frappe.get_all", return_value=[frappe._dict(name="GROUP-1", parent="RIN-1", docstatus=1)]),
			patch("frappe.db.set_value") as update,
		):
			with self.assertRaises(frappe.ValidationError):
				prepare_return_quality_inspection_removal(frappe._dict(name="QI-1"))
			update.assert_not_called()
