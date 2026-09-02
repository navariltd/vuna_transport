# Copyright (c) 2026, Yovuna Technologies Limited and Contributors
# See license.txt

from collections import defaultdict
from types import SimpleNamespace

from frappe.tests import UnitTestCase
from frappe.utils import getdate

from vuna_transport.services.fleet.vehicle_compliance_service import (
	_evaluate_vehicle_compliance,
)


class TestVehicleComplianceService(UnitTestCase):
	def setUp(self):
		self.vehicle = SimpleNamespace(
			name="KDA 123A",
			company="Vuna Foods",
			vuna_vehicle_category="8 Ton Reefer",
			vuna_vehicle_owner="Company",
			policy_no="POL-1",
			start_date="2026-01-01",
			end_date="2027-01-01",
		)
		self.requirement = SimpleNamespace(
			name="Inspection Certificate",
			document_type_name="Inspection Certificate",
			company=None,
			vehicle_category=None,
			vehicle_owner=None,
			mandatory_for_dispatch=1,
			requires_expiry_date=1,
			warning_days_before_expiry=30,
		)
		self.evaluation_date = getdate("2026-08-24")

	def test_valid_mandatory_document_is_compliant(self):
		document = self._document(expiry_date="2027-01-01")
		result = self._evaluate([self.requirement], [document])
		self.assertTrue(result.compliant)
		self.assertEqual(result.documents[0].status, "Valid")

	def test_expired_mandatory_document_blocks_dispatch(self):
		document = self._document(expiry_date="2026-08-01")
		result = self._evaluate([self.requirement], [document])
		self.assertFalse(result.compliant)
		self.assertEqual(result.reasons[0].code, "DOCUMENT_INVALID")

	def test_expiring_document_warns_without_blocking(self):
		document = self._document(expiry_date="2026-09-01")
		result = self._evaluate([self.requirement], [document])
		self.assertTrue(result.compliant)
		self.assertEqual(result.documents[0].status, "Expiring Soon")
		self.assertEqual(result.warnings[0].code, "DOCUMENT_EXPIRING_SOON")

	def test_optional_expired_document_does_not_block(self):
		self.requirement.mandatory_for_dispatch = 0
		document = self._document(expiry_date="2026-08-01")
		result = self._evaluate([self.requirement], [document])
		self.assertTrue(result.compliant)

	def test_category_specific_requirement_is_ignored_for_other_category(self):
		self.requirement.vehicle_category = "3 Ton Truck"
		result = self._evaluate([self.requirement], [])
		self.assertTrue(result.compliant)
		self.assertFalse(result.documents)

	def test_ownership_specific_requirement_is_enforced(self):
		self.requirement.vehicle_owner = "Company"
		result = self._evaluate([self.requirement], [])
		self.assertFalse(result.compliant)
		self.assertEqual(result.reasons[0].code, "DOCUMENT_MISSING")

	def test_standard_vehicle_insurance_satisfies_insurance_requirement(self):
		self.requirement.name = "Insurance"
		self.requirement.document_type_name = "Insurance"
		result = self._evaluate([self.requirement], [])
		self.assertTrue(result.compliant)
		self.assertEqual(result.documents[0].source, "Vehicle")

	def test_expired_standard_vehicle_insurance_blocks_dispatch(self):
		self.requirement.name = "Insurance"
		self.requirement.document_type_name = "Insurance"
		self.vehicle.end_date = "2026-08-01"
		result = self._evaluate([self.requirement], [])
		self.assertFalse(result.compliant)
		self.assertEqual(result.reasons[0].code, "INSURANCE_EXPIRED")

	def _evaluate(self, requirements, documents):
		by_vehicle_and_type = defaultdict(list)
		for document in documents:
			by_vehicle_and_type[(document.vehicle, document.document_type)].append(
				document
			)
		return _evaluate_vehicle_compliance(
			self.vehicle,
			requirements,
			by_vehicle_and_type,
			self.evaluation_date,
		)

	def _document(self, expiry_date):
		return SimpleNamespace(
			name="VD-1",
			vehicle=self.vehicle.name,
			document_type=self.requirement.name,
			document_number="DOC-1",
			issue_date="2026-01-01",
			expiry_date=expiry_date,
			suspended=0,
			cancelled=0,
		)
