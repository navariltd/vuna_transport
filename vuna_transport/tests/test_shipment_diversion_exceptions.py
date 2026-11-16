"""Actual receipt and follow-up reservation boundaries; no stock posting in tests."""

from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from vuna_transport.services.execution import shipment_diversion_exceptions as service
from vuna_transport.services.execution import shipment_diversion_followup as followup
from vuna_transport.services.execution.shipment_diversion_service import available_diversion_stock
from vuna_transport.tests.test_shipment_diversion_documents import Document


class TestDiversionExceptions(TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(service, "frappe"))
		self.framework._dict = frappe._dict
		self.framework.throw.side_effect = lambda message, *a, **k: self.fail_rule(message)
		self.enterContext(patch.object(service, "_", side_effect=lambda message: message))
		self.item = Document(
			name="SDI",
			item_code="Milk",
			qty=10,
			stock_qty=240,
			uom="Carton",
			stock_uom="Pcs",
			conversion_factor=24,
			batch_no="B1",
			serial_no="",
		)
		self.doc = Document(
			name="SD",
			execution_task="TASK",
			company="Company",
			vehicle_warehouse="Vehicle",
			destination_warehouse="Depot",
			items=[self.item],
		)
		self.actual = Document(
			name="PI",
			source_row="SDI",
			delivered_qty=6,
			rejected_qty=4,
			serial_no="",
			depot_received_rejected_qty=1,
			depot_received_rejected_serial_no="",
			rejected_serial_no="",
			conversion_factor=24,
		)
		self.proof = Document(
			name="POD",
			docstatus=1,
			transport_trip_task="TASK",
			rejected_goods_warehouse="Quarantine",
			items=[self.actual],
		)
		self.proof.return_request = "RR"
		self.request = Document(
			name="RR", items=[Document(name="RRI", source_pod_item="PI", custody_warehouse="Quarantine")]
		)
		self.framework.db.get_value.return_value = "RRI"
		self.entry = Document(
			name="SE",
			company="Company",
			vuna_shipment_diversion="SD",
			purpose="Material Transfer",
			add_to_transit=0,
			outgoing_stock_entry=None,
			items=[],
		)
		self.bundle = self.enterContext(patch.object(service, "make_bundle"))
		self.identity = self.enterContext(
			patch(
				"vuna_transport.services.execution.shipment_diversion_documents._identity",
				side_effect=lambda row, qty: {
					"B1": {"stock_qty": qty, "serials": (row.get("serial_no") or "").splitlines()}
				},
			)
		)

	@staticmethod
	def fail_rule(message):
		raise ValueError(message)

	def test_partial_receipt_moves_only_accepted_and_depot_held_rejections(self):
		self.assertTrue(service.apply_receipt(self.entry, self.doc, self.proof, self.request))
		self.assertEqual(
			[(row.qty, row.transfer_qty, row.t_warehouse) for row in self.entry.items],
			[(6, 144, "Depot"), (1, 24, "Quarantine")],
		)
		service.validate_receipt(self.entry, self.doc, self.proof)
		self.assertEqual(self.item.stock_qty - sum(row.transfer_qty for row in self.entry.items), 72)
		self.assertEqual(self.item.qty, 10)

	def test_all_rejected_remaining_on_vehicle_creates_no_zero_or_full_stock_receipt(self):
		self.actual.delivered_qty, self.actual.rejected_qty, self.actual.depot_received_rejected_qty = (
			0,
			10,
			0,
		)
		self.assertFalse(service.apply_receipt(self.entry, self.doc, self.proof))
		self.assertEqual(self.entry.items, [])
		self.bundle.assert_not_called()

	def test_all_rejected_received_by_depot_moves_to_holding_not_saleable_stock(self):
		self.actual.delivered_qty, self.actual.rejected_qty, self.actual.depot_received_rejected_qty = (
			0,
			10,
			10,
		)
		service.apply_receipt(self.entry, self.doc, self.proof, self.request)
		self.assertEqual(
			[(row.qty, row.t_warehouse, row.vuna_diversion_result) for row in self.entry.items],
			[(10, "Quarantine", "Rejected")],
		)

	def test_missing_goods_do_not_become_a_receipt_or_known_vehicle_rejection(self):
		self.actual.delivered_qty, self.actual.rejected_qty, self.actual.depot_received_rejected_qty = 6, 2, 0
		service.apply_receipt(self.entry, self.doc, self.proof)
		self.assertEqual(sum(row.qty for row in self.entry.items), 6)
		self.assertEqual(len(self.entry.items), 1)

	def test_wrong_quantity_destination_and_duplicate_rows_are_rejected(self):
		service.apply_receipt(self.entry, self.doc, self.proof, self.request)
		self.entry.items[0].qty = 10
		with self.assertRaises(ValueError):
			service.validate_receipt(self.entry, self.doc, self.proof)
		self.entry.items[0].qty = 6
		self.entry.items[1].t_warehouse = "Saleable"
		with self.assertRaises(ValueError):
			service.validate_receipt(self.entry, self.doc, self.proof)
		self.entry.items[1].t_warehouse = "Quarantine"
		self.entry.items.append(self.entry.items[0])
		with self.assertRaises(ValueError):
			service.validate_receipt(self.entry, self.doc, self.proof)

	def test_draft_proof_cannot_authorize_stock_posting(self):
		service.apply_receipt(self.entry, self.doc, self.proof, self.request)
		self.proof.docstatus = 0
		with self.assertRaises(ValueError):
			service.validate_receipt(self.entry, self.doc, self.proof)

	def test_rejected_stock_cannot_hide_its_return_case_or_count_as_accepted(self):
		service.apply_receipt(self.entry, self.doc, self.proof, self.request)
		self.entry.items[1].vuna_return_request_item = None
		with self.assertRaisesRegex(ValueError, "custody item link"):
			service.validate_receipt(self.entry, self.doc, self.proof)

	def test_warehouse_return_and_followup_share_the_same_vehicle_quantity(self):
		request = Document(name="RR", proof_of_delivery="POD")
		row = Document(
			name="RRI",
			requested_return_qty=5,
			conversion_factor=24,
			serial_no="",
			custody_warehouse="Vehicle",
			source_pod_item="PI",
		)
		self.framework.db.get_value.side_effect = (
			lambda doctype, *args, **kwargs: "Transit" if doctype == "Warehouse" else "SDI"
		)
		self.framework.db.sql.side_effect = [
			[frappe._dict(transfer_qty=48, serial_no="")],
			[frappe._dict(stock_qty=24, serial_no="")],
		]
		qty, _ = service.return_item_available(request, row, "OWN-RECEIPT")
		self.assertEqual(qty, 48)
		self.assertEqual(self.framework.db.sql.call_args_list[0].args[1], ("RRI", "OWN-RECEIPT"))

	def test_receipt_reversal_excludes_only_its_owned_proof_not_unrelated_evidence(self):
		self.framework.get_doc.return_value = self.doc
		self.framework.get_all.return_value = [
			frappe._dict(
				transport_trip_task="Other Task", delivery_trip="Other Trip", proof_type="Transfer Receipt"
			)
		]
		with self.assertRaisesRegex(ValueError, "Other submitted proof"):
			service.prepare_receipt_reversal(self.entry)
		self.framework.get_all.return_value = []
		service.prepare_receipt_reversal(self.entry)
		self.assertEqual(self.entry.ignore_linked_doctypes, ["Proof of Delivery"])

	def test_serials_received_by_depot_are_not_available_for_vehicle_followup(self):
		self.actual.serial_no = "S1\nS2\nS3\nS4\nS5\nS6"
		self.actual.depot_received_rejected_serial_no = "S7\nS8"
		self.actual.depot_received_rejected_qty = 2
		groups = service.expected_receipts(self.doc, self.proof)
		self.assertEqual(groups[("SDI", "Accepted")].serial_no, self.actual.serial_no)
		self.assertEqual(groups[("SDI", "Rejected")].serial_no, "S7\nS8")

	def test_reversal_requires_followups_and_return_case_to_be_reversed_first(self):
		self.framework.db.exists.return_value = True
		with self.assertRaisesRegex(ValueError, "follow-up diversions"):
			service.guard_proof_reversal(self.proof)
		self.framework.db.exists.side_effect = [False, True]
		with self.assertRaisesRegex(ValueError, "rejection Return Request"):
			service.guard_proof_reversal(self.proof)
		self.framework.db.exists.side_effect = [False, False]
		self.proof.dispatch_stock_entry = "SE"
		self.framework.db.get_value.return_value = 1
		with self.assertRaisesRegex(ValueError, "posted depot receipt"):
			service.guard_proof_reversal(self.proof)


class TestDiversionFollowup(TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(followup, "frappe"))
		self.framework._dict = frappe._dict
		self.framework.throw.side_effect = lambda message, *a, **k: TestDiversionExceptions.fail_rule(message)
		self.enterContext(patch.object(followup, "_", side_effect=lambda message: message))
		self.enterContext(patch.object(followup, "require_document_permission"))
		self.enterContext(patch.object(followup, "root_diversion"))
		self.parent_row = Document(name="PARENT-ITEM", return_request_item="RRI", conversion_factor=24)
		self.parent = Document(
			name="PARENT",
			docstatus=1,
			execution_task="TASK",
			company="Company",
			vehicle_warehouse="Vehicle",
			items=[self.parent_row],
		)
		self.actual = Document(
			source_row="PARENT-ITEM",
			rejected_qty=4,
			depot_received_rejected_qty=1,
			rejected_serial_no="S1\nS2\nS3\nS4",
			depot_received_rejected_serial_no="S4",
		)
		self.proof = Document(
			name="POD",
			docstatus=1,
			transport_trip_task="TASK",
			proof_type="Transfer Receipt",
			company="Company",
			items=[self.actual],
		)
		self.framework.get_doc.side_effect = (
			lambda doctype, *a, **k: self.parent if doctype == "Shipment Diversion" else self.proof
		)
		self.framework.db.sql.side_effect = [[], []]
		self.doc = Document(name="NEW", previous_diversion="PARENT", previous_proof_of_delivery="POD")
		self.context = {
			"items": [{"return_request_item": "RRI", "proof_of_delivery_item": "ORIGINAL-POD-ITEM"}]
		}

	def test_only_rejected_goods_confirmed_still_on_vehicle_can_be_redirected(self):
		items, reservations = followup.followup_candidates(self.doc, self.context)
		item = items["RRI"]
		self.assertEqual(item["stock_qty"], 72)
		self.assertEqual(set(item["rejected_serial_no"].splitlines()), {"S1", "S2", "S3"})
		self.assertEqual(available_diversion_stock(item, reservations)[0], 72)

	def test_draft_and_approved_followups_share_one_remaining_quantity_budget(self):
		self.framework.db.sql.side_effect = [
			[
				frappe._dict(
					return_request_item="RRI",
					proof_of_delivery_item="ORIGINAL-POD-ITEM",
					stock_qty=48,
					serial_no="S1\nS2",
				)
			],
			[],
		]
		items, reservations = followup.followup_candidates(self.doc, self.context)
		remaining, serials = available_diversion_stock(items["RRI"], reservations)
		self.assertEqual(remaining, 24)
		self.assertEqual(serials, {"S1", "S2"})
		query = self.framework.db.sql.call_args_list[0].args[0]
		self.assertIn("docstatus in (0,1)", query)
		self.assertIn("for update", query)

	def test_depot_held_and_missing_goods_do_not_expand_followup_budget(self):
		self.actual.rejected_qty = self.actual.depot_received_rejected_qty = 4
		items, _ = followup.followup_candidates(self.doc, self.context)
		self.assertFalse(items)

	def test_cancelled_or_foreign_parent_proof_is_rejected(self):
		self.proof.docstatus = 2
		with self.assertRaises(ValueError):
			followup.followup_candidates(self.doc, self.context)
		self.proof.docstatus, self.proof.transport_trip_task = 1, "Other Task"
		with self.assertRaises(ValueError):
			followup.followup_candidates(self.doc, self.context)


class TestDiversionProgress(TestCase):
	def test_customer_followup_without_an_onward_note_cannot_count_unrelated_documents(self):
		self.framework.get_all.side_effect = None
		self.framework.get_all.return_value = ["CHILD"]
		self.framework.get_doc.side_effect = None
		self.framework.get_doc.return_value = Document(
			name="CHILD", execution_task="TASK", destination_type="Customer", onward_delivery_note=None
		)
		self.assertEqual(followup.completed_followup_stock("PARENT"), {})
		self.framework.db.exists.assert_not_called()
		self.framework.db.get_value.assert_not_called()

	def setUp(self):
		self.framework = self.enterContext(patch.object(followup, "frappe"))
		self.framework.throw.side_effect = lambda message, *a, **k: TestDiversionExceptions.fail_rule(message)
		self.enterContext(patch.object(followup, "_", side_effect=lambda message: message))
		self.enterContext(
			patch(
				"vuna_transport.services.execution.return_service._has_completed_commercial_resolution",
				return_value=False,
			)
		)
		self.request = Document(
			name="RR",
			company="Company",
			customer="A",
			delivery_note="ORIGINAL",
			return_stock_document=None,
			docstatus=1,
			status="Awaiting Sales Return",
			items=[
				Document(name="RRI", delivery_note_item="DNI", requested_return_qty=10, conversion_factor=24)
			],
		)
		self.receipts = {
			name: Document(
				name=name,
				docstatus=1,
				company="Company",
				customer="A",
				is_return=1,
				return_against="ORIGINAL",
				items=[
					Document(
						name=name + "-ITEM",
						dn_detail="DNI",
						stock_qty=-qty,
						vuna_diversion_item="ITEM-" + name,
					)
				],
			)
			for name, qty in (("SR1", 72), ("SR2", 168))
		}
		self.framework.get_all.side_effect = (
			lambda doctype, **kw: list(self.receipts)
			if doctype == "Delivery Note"
			else [kw["filters"]["sales_return"]]
		)
		self.framework.get_doc.side_effect = (
			lambda doctype, name: self.receipts[name] if doctype == "Delivery Note" else Document(name=name)
		)
		self.framework.db.get_value.return_value = None
		self.framework.db.exists.return_value = True
		self.enterContext(
			patch.object(
				followup,
				"settled_stock",
				side_effect=lambda doc: {"ITEM-" + doc.name: 72 if doc.name == "SR1" else 168},
			)
		)

	def test_all_partial_vehicle_sales_returns_are_counted_without_a_single_forward_link(self):
		state = followup.customer_return_state(self.request)
		self.assertEqual(
			(state["returned_qty"], state["diverted_qty"], state["return_difference_qty"]), (10, 10, 0)
		)
		self.assertEqual(state["status"], "Awaiting Commercial Resolution")
		self.framework.db.set_value.assert_not_called()

	def test_transport_completion_does_not_skip_quality_or_create_customer_credit(self):
		self.framework.db.exists.return_value = False
		state = followup.customer_return_state(self.request)
		self.assertEqual(state["status"], "Awaiting Inspection")
		self.assertFalse(state["commercial_complete"])

	def test_unsubmitted_partial_receipt_is_not_reported_as_received(self):
		self.receipts["SR2"].docstatus = 0
		state = followup.customer_return_state(self.request)
		self.assertEqual((state["returned_qty"], state["return_difference_qty"]), (3, 7))
		self.assertEqual(state["status"], "Awaiting Return Submission")

	def test_unrelated_customer_receipt_cannot_settle_the_return_case(self):
		self.receipts["SR2"].customer = "OTHER"
		with self.assertRaises(ValueError):
			followup.customer_return_state(self.request)

	def test_depot_case_distinguishes_diverted_goods_from_returned_goods(self):
		from vuna_transport.services.execution import depot_return_service as depot

		request = Document(
			name="DEPOT-RR",
			docstatus=1,
			shipment_diversion="PARENT",
			proof_of_delivery="POD",
			return_warehouse="Returns",
			items=[
				Document(
					name="RRI",
					requested_return_qty=5,
					conversion_factor=24,
					source_pod_item="PI",
					custody_warehouse="Vehicle",
				)
			],
		)
		with (
			patch.object(depot, "frappe") as framework,
			patch.object(depot, "inspection_progress", return_value={"posted": 0, "inspected": 0}),
			patch.object(followup, "completed_followup_stock", return_value={"SDI": 120}),
		):
			framework.db.sql.return_value = []
			framework.get_doc.side_effect = (
				lambda dt, name: Document(name="PARENT", vehicle_warehouse="Vehicle")
				if dt == "Shipment Diversion"
				else Document(items=[Document(name="PI", source_row="SDI")])
			)
			state = depot.depot_return_state(request)
		self.assertEqual(
			(state["returned_qty"], state["diverted_qty"], state["return_difference_qty"]), (0, 5, 0)
		)
		self.assertEqual(state["status"], "Resolved")
