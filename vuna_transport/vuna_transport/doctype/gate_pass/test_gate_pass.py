from unittest.mock import patch

from frappe.exceptions import ValidationError
from frappe.tests import UnitTestCase

from vuna_transport.vuna_transport.doctype.gate_pass.gate_pass import GatePass


class TestGatePass(UnitTestCase):
	def test_gate_pass_requires_execution_reference(self):
		gate_pass = GatePass({"doctype": "Gate Pass", "direction": "Inbound"})

		with self.assertRaises(ValidationError):
			gate_pass.validate()

	@patch("vuna_transport.vuna_transport.doctype.gate_pass.gate_pass.frappe.get_all", return_value=[])
	@patch(
		"vuna_transport.vuna_transport.doctype.gate_pass.gate_pass.get_delivery_trip_load_names",
		return_value=["DL-1"],
	)
	@patch("vuna_transport.vuna_transport.doctype.gate_pass.gate_pass.frappe.get_doc")
	def test_outbound_gate_pass_requires_submitted_delivery_note(self, _get_doc, _get_load_names, _get_all):
		gate_pass = GatePass(
			{
				"doctype": "Gate Pass",
				"direction": "Outbound",
				"delivery_trip": "DT-1",
				"vehicle": "KDA 123A",
				"driver": "DR-1",
			}
		)

		with self.assertRaises(ValidationError):
			gate_pass._validate_outbound_documents()

	@patch(
		"vuna_transport.vuna_transport.doctype.gate_pass.gate_pass.frappe.get_all",
		return_value=["DN-1"],
	)
	@patch(
		"vuna_transport.vuna_transport.doctype.gate_pass.gate_pass.get_delivery_trip_load_names",
		return_value=["DL-1"],
	)
	@patch("vuna_transport.vuna_transport.doctype.gate_pass.gate_pass.frappe.get_doc")
	def test_outbound_gate_pass_accepts_submitted_delivery_note(self, _get_doc, _get_load_names, _get_all):
		gate_pass = GatePass(
			{
				"doctype": "Gate Pass",
				"direction": "Outbound",
				"delivery_trip": "DT-1",
				"vehicle": "KDA 123A",
				"driver": "DR-1",
			}
		)

		gate_pass._validate_outbound_documents()

	def test_gate_in_cannot_precede_gate_out(self):
		gate_pass = GatePass(
			{
				"doctype": "Gate Pass",
				"direction": "Inbound",
				"delivery_trip": "DT-1",
				"gate_out_datetime": "2026-09-17 12:00:00",
				"gate_in_datetime": "2026-09-17 11:00:00",
			}
		)

		with (
			patch.object(gate_pass, "_validate_references"),
			patch.object(gate_pass, "_populate_execution_context"),
		):
			with self.assertRaises(ValidationError):
				gate_pass.validate()

	@patch(
		"vuna_transport.vuna_transport.doctype.gate_pass.gate_pass.now_datetime",
		return_value="2026-09-17 13:00:00",
	)
	def test_gate_out_and_gate_in_are_controlled_transitions(self, _now):
		gate_pass = GatePass(
			{
				"doctype": "Gate Pass",
				"docstatus": 1,
				"status": "Issued",
				"delivery_trip": "DT-1",
				"vehicle": "KDA 123A",
				"driver": "DR-1",
			}
		)
		with patch.object(gate_pass, "check_permission"), patch.object(gate_pass, "db_set") as db_set:
			gate_pass.mark_gate_out()
			self.assertEqual(gate_pass.status, "Exited")
			db_set.assert_called_once()

		gate_pass.status = "Exited"
		with patch.object(gate_pass, "check_permission"), patch.object(gate_pass, "db_set") as db_set:
			gate_pass.mark_gate_in()
			self.assertEqual(gate_pass.status, "Returned")
			db_set.assert_called_once()
