import unittest
from unittest.mock import patch

from vuna_transport.patches.rename_transport_load_doctypes import execute


class TestTransportLoadRename(unittest.TestCase):
	@patch("vuna_transport.patches.rename_transport_load_doctypes.frappe")
	def test_existing_tables_are_renamed_in_dependency_order(self, framework):
		framework.db.exists.side_effect = lambda doctype, name: name in {
			"Delivery Load",
			"Delivery Load Order",
		}
		execute()
		self.assertEqual(
			[call.args for call in framework.rename_doc.call_args_list],
			[
				("DocType", "Delivery Load Order", "Transport Load Item"),
				("DocType", "Delivery Load", "Transport Load"),
			],
		)

	@patch("vuna_transport.patches.rename_transport_load_doctypes.frappe")
	def test_patch_is_noop_without_legacy_doctypes(self, framework):
		framework.db.exists.return_value = False
		execute()
		framework.rename_doc.assert_not_called()

	@patch("vuna_transport.patches.rename_transport_load_doctypes.frappe")
	def test_collision_fails_instead_of_merging_records(self, framework):
		framework.db.exists.return_value = True
		framework.throw.side_effect = ValueError
		with self.assertRaises(ValueError):
			execute()
		framework.rename_doc.assert_not_called()
