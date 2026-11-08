"""Device handover keeps one active owner and preserves an administrative trail."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, call, patch

import frappe

from vuna_transport.services.mobile import mobile_device_service as mobile
from vuna_transport.services.mobile import mobile_notification_service as notifications
from vuna_transport.vuna_transport.doctype.transport_mobile_device import transport_mobile_device as devices


def throw(message, exception=ValueError):
	raise exception(message)


class TestMobileDeviceReassignment(TestCase):
	def setUp(self):
		self.framework = self.enterContext(patch.object(devices, "frappe"))
		self.framework.throw.side_effect = throw
		self.framework.PermissionError = PermissionError
		self.framework.get_roles.return_value = ["Transport Manager"]
		self.framework.session.user = "manager@example.test"
		self.enterContext(patch.object(devices, "_", side_effect=lambda message: message))
		self.lock = self.enterContext(patch.object(devices, "_lock_mobile_user"))
		self.company_access = self.enterContext(patch.object(devices, "require_company_access"))
		self.device = SimpleNamespace(
			name="DEVICE",
			user="old@example.test",
			status="Active",
			installation_id="INSTALL",
			device_identifier="PHONE",
			latest_installation_id="OLD-INSTALL",
			push_token="old-token",
			push_provider="expo",
			push_notifications_enabled=1,
			push_token_updated_at="2026-11-08",
			last_seen_at="2026-11-08",
			flags=frappe._dict(),
			check_permission=MagicMock(),
			reload=MagicMock(),
			save=MagicMock(),
			add_comment=MagicMock(),
		)
		self.other = MagicMock()
		self.framework.get_doc.side_effect = (
			lambda doctype, name: self.device if name == "DEVICE" else self.other
		)
		self.old_drivers = [frappe._dict(name="OLD", employee="EMP-OLD")]
		self.new_drivers = [frappe._dict(name="NEW", employee="EMP-NEW", status="Active")]
		self.replaced = ["OTHER-DEVICE"]
		self.framework.get_all.side_effect = self.rows
		self.framework.db.get_value.side_effect = lambda doctype, name, field, **kw: (
			1 if doctype == "User" else "Company" if doctype == "Employee" else "DEVICE"
		)

	def rows(self, doctype, **kwargs):
		if doctype == "Driver":
			return self.new_drivers if kwargs["filters"]["user"] == "new@example.test" else self.old_drivers
		return self.replaced

	def reassign(self, **kwargs):
		return devices.reassign_mobile_device(
			**{
				"device_name": "DEVICE",
				"expected_user": "old@example.test",
				"new_user": "new@example.test",
				"reason": "Phone sold to another driver",
				"local_data_cleared": True,
				**kwargs,
			}
		)

	def test_reassignment_changes_owner_activates_device_and_revokes_previous_phone(self):
		result = self.reassign()
		self.assertEqual((self.device.user, self.device.status), ("new@example.test", "Active"))
		self.assertEqual(result["revoked_devices"], ["OTHER-DEVICE"])
		self.device.check_permission.assert_called_once_with("write")
		self.other.check_permission.assert_called_once_with("write")
		self.other.db_set.assert_called_once_with("status", "Revoked", update_modified=True)
		self.device.save.assert_called_once_with()
		self.framework.db.commit.assert_not_called()

	def test_credentials_activity_and_push_token_are_not_carried_to_new_owner(self):
		self.reassign()
		for field in (
			"push_token",
			"push_provider",
			"push_token_updated_at",
			"last_seen_at",
			"latest_installation_id",
		):
			self.assertIsNone(getattr(self.device, field))
		self.assertEqual(self.device.push_notifications_enabled, 0)
		self.assertEqual((self.device.installation_id, self.device.device_identifier), ("INSTALL", "PHONE"))

	def test_history_records_actor_previous_and_new_owner_reason_and_reset_confirmation(self):
		self.reassign(reason="Sold <script>alert(1)</script>")
		comment = self.device.add_comment.call_args.args[1]
		for text in (
			"old@example.test",
			"new@example.test",
			"manager@example.test",
			"clearance confirmed",
			"&lt;script&gt;",
		):
			self.assertIn(text, comment)
		self.assertNotIn("<script>", comment)

	def test_roles_and_document_write_permission_are_both_enforced(self):
		self.framework.get_roles.return_value = ["Driver"]
		with self.assertRaises(PermissionError):
			self.reassign()
		self.framework.get_doc.assert_not_called()
		self.framework.get_roles.return_value = ["System Manager"]
		self.device.check_permission.side_effect = PermissionError
		with self.assertRaises(PermissionError):
			self.reassign()
		self.device.save.assert_not_called()

	def test_sync_and_local_reset_acknowledgement_is_required(self):
		for value in (False, 0, "0"):
			with self.assertRaisesRegex(ValueError, "Confirm"):
				self.reassign(local_data_cleared=value)
		self.framework.get_doc.assert_not_called()

	def test_reason_is_required_and_bounded(self):
		for value in ("", "  ", "x" * 1001):
			with self.assertRaisesRegex(ValueError, "reason"):
				self.reassign(reason=value)
		self.device.save.assert_not_called()

	def test_same_user_is_not_a_reassignment(self):
		with self.assertRaisesRegex(ValueError, "different"):
			self.reassign(new_user="old@example.test")

	def test_stale_form_cannot_overwrite_a_new_owner(self):
		self.device.reload.side_effect = lambda: setattr(self.device, "user", "someone@example.test")
		with self.assertRaisesRegex(ValueError, "registered user changed"):
			self.reassign()
		self.device.save.assert_not_called()
		self.other.db_set.assert_not_called()

	def test_user_locks_are_sorted_and_device_is_reloaded_under_lock(self):
		self.reassign()
		self.assertEqual(self.lock.call_args_list, [call("new@example.test"), call("old@example.test")])
		self.device.reload.assert_called_once_with()
		self.framework.db.get_value.assert_any_call(
			"Transport Mobile Device", "DEVICE", "name", for_update=True
		)

	def test_disabled_and_guest_accounts_are_rejected(self):
		self.framework.db.get_value.side_effect = lambda *args, **kwargs: 0
		with self.assertRaisesRegex(ValueError, "enabled"):
			self.reassign()
		with self.assertRaisesRegex(ValueError, "enabled"):
			self.reassign(new_user="Guest")

	def test_target_must_have_one_active_driver_matching_mobile_profile_rules(self):
		for rows in ([], [frappe._dict(status="Inactive")], self.new_drivers * 2):
			self.new_drivers = rows
			with self.assertRaisesRegex(ValueError, "exactly one active Driver"):
				self.reassign()

	def test_company_access_is_enforced_and_cross_company_reassignment_is_rejected(self):
		self.company_access.side_effect = PermissionError
		with self.assertRaises(PermissionError):
			self.reassign()
		self.company_access.side_effect = None
		self.framework.db.get_value.side_effect = lambda dt, name, field, **kw: (
			("Other Company" if name == "EMP-NEW" else "Company") if dt == "Employee" else 1
		)
		with self.assertRaisesRegex(ValueError, "same company"):
			self.reassign()

	def test_driver_without_an_employee_is_supported(self):
		self.old_drivers, self.new_drivers = [], [frappe._dict(name="NEW", employee=None, status="Active")]
		self.reassign()
		self.device.save.assert_called_once()

	def test_failed_save_does_not_leave_ownership_bypass_flag_set(self):
		self.device.save.side_effect = ValueError("save failed")
		with self.assertRaisesRegex(ValueError, "save failed"):
			self.reassign()
		self.assertNotIn("mobile_device_reassignment", self.device.flags)

	def test_direct_ownership_edit_cannot_bypass_the_action(self):
		self.device.user = "new@example.test"
		self.device.get_doc_before_save = lambda: frappe._dict(user="old@example.test")
		self.device.flags.mobile_device_reassignment = True
		with self.assertRaisesRegex(ValueError, "Use Reassign Device"):
			devices.TransportMobileDevice.validate(self.device)


class TestReassignedDeviceAccess(TestCase):
	def test_queued_notifications_cannot_read_tokens_after_owner_or_status_changes(self):
		for values in (
			{"user": "new@example.test", "status": "Active", "push_notifications_enabled": 1},
			{"user": "old@example.test", "status": "Revoked", "push_notifications_enabled": 1},
			{"user": "old@example.test", "status": "Active", "push_notifications_enabled": 0},
		):
			device = SimpleNamespace(**values, get_password=MagicMock())
			with (
				patch.object(notifications, "frappe") as framework,
				patch.object(notifications.requests, "post") as post,
			):
				framework.get_all.return_value = ["DEVICE"]
				framework.get_doc.return_value = device
				notifications.send_push_notification("old@example.test", "Title", "Body", "/dashboard")
				device.get_password.assert_not_called()
				post.assert_not_called()

	def test_previous_user_loses_access_but_new_user_can_use_the_same_device(self):
		with (
			patch.object(mobile, "frappe") as framework,
			patch.object(
				mobile,
				"_find_device",
				return_value=frappe._dict(name="DEVICE", user="new@example.test", status="Active"),
			),
		):
			framework.session.user = "old@example.test"
			self.assertEqual(mobile._get_active_device("INSTALL", "PHONE")["code"], "DEVICE_NOT_REGISTERED")
			framework.session.user = "new@example.test"
			framework.db.exists.return_value = False
			self.assertIsNone(mobile._get_active_device("INSTALL", "PHONE"))

	def test_push_registration_locks_the_user_before_checking_device_ownership(self):
		events = []
		with (
			patch.object(mobile, "_lock_mobile_user", side_effect=lambda user: events.append("lock")),
			patch.object(
				mobile,
				"_get_active_device",
				side_effect=lambda *args: events.append("check") or {"message": "revoked"},
			),
			patch.object(mobile, "frappe") as framework,
		):
			framework.throw.side_effect = PermissionError
			with self.assertRaises(PermissionError):
				mobile.register_mobile_push_token("INSTALL", "token")
		self.assertEqual(events, ["lock", "check"])


def run_reassignment_smoke():
	"""Disposable users/devices verify real Version/password behavior; always rollback."""
	original_user = frappe.session.user
	try:
		frappe.set_user("Administrator")
		tag = frappe.generate_hash(length=12)
		users = [f"device-test-{tag}-{index}@example.test" for index in (1, 2)]
		for index, user in enumerate(users):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": user,
					"first_name": f"Device test {index}",
					"enabled": 1,
					"send_welcome_email": 0,
					"user_type": "Website User",
				}
			).insert(ignore_permissions=True)
			frappe.get_doc(
				{
					"doctype": "Driver",
					"full_name": f"Device test {index}",
					"user": user,
					"status": "Active",
					"custom_national_id": str(
						90_000_000 + (int.from_bytes(tag.encode(), "little") + index) % 9_000_000
					),
					"custom_emergency_contact": "+254700000000",
				}
			).insert(ignore_permissions=True)
		phones = []
		for index, user in enumerate(users):
			phones.append(
				frappe.get_doc(
					{
						"doctype": "Transport Mobile Device",
						"installation_id": f"DEVICE-TEST-{tag}-{index}",
						"device_identifier": f"PHONE-TEST-{tag}-{index}",
						"user": user,
						"status": "Active",
						"push_token": f"test-push-token-{tag}-{index}",
						"push_provider": "expo",
						"push_notifications_enabled": 1,
					}
				).insert()
			)
		first, other = phones
		result = devices.reassign_mobile_device(
			first.name, users[1], users[0], "Disposable test handover", True
		)
		first.reload()
		other.reload()
		assert first.user == users[1] and first.status == "Active"
		assert other.status == "Revoked" and result["revoked_devices"] == [other.name]
		assert not first.get_password("push_token", raise_exception=False)
		assert not first.push_notifications_enabled
		versions = frappe.get_all(
			"Version", filters={"ref_doctype": first.doctype, "docname": first.name}, pluck="data"
		)
		assert any(
			["user", users[0], users[1]] in frappe.parse_json(data).get("changed", []) for data in versions
		)
		assert frappe.db.exists(
			"Comment",
			{"reference_doctype": first.doctype, "reference_name": first.name, "comment_type": "Info"},
		)
		frappe.set_user(users[0])
		assert (
			mobile._get_active_device(first.installation_id, first.device_identifier)["code"]
			== "DEVICE_NOT_REGISTERED"
		)
		assert not mobile.bootstrap_device(
			installation_id=first.installation_id, device_identifier=first.device_identifier
		)["allowed"]
		frappe.set_user(users[1])
		assert (
			mobile._get_active_device(other.installation_id, other.device_identifier)["code"]
			== "DEVICE_NOT_ACTIVE"
		)
		assert mobile.bootstrap_device(
			installation_id=f"FRESH-{tag}", device_identifier=first.device_identifier
		)["allowed"]
		return {
			"reassignment": "passed",
			"previous_owner_denied": "passed",
			"previous_target_device_revoked": "passed",
			"new_owner_bootstrap": "passed",
			"encrypted_push_token_removed": "passed",
			"ownership_version_preserved": "passed",
			"all_changes_rolled_back": True,
		}
	finally:
		frappe.db.rollback()
		frappe.set_user(original_user)
