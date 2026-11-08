// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Transport Mobile Device", {
	refresh(frm) {
		if (
			frm.is_new() ||
			!(frappe.user.has_role("System Manager") || frappe.user.has_role("Transport Manager"))
		) {
			return;
		}

		if (frm.doc.status !== "Active") {
			frm.add_custom_button(__("Activate Device"), () =>
				manage_device(frm, "activate_mobile_device")
			);
		}
		if (frm.doc.status !== "Revoked") {
			frm.add_custom_button(__("Revoke Device"), () =>
				manage_device(frm, "revoke_mobile_device")
			);
		}
		frm.add_custom_button(__("Reassign Device"), () => reassign_device(frm));
	},
});

function manage_device(frm, method) {
	frappe.call({
		method: `vuna_transport.vuna_transport.doctype.transport_mobile_device.transport_mobile_device.${method}`,
		args: { device_name: frm.doc.name },
		freeze: true,
		callback: () => frm.reload_doc(),
	});
}

function reassign_device(frm) {
	if (frm.is_dirty()) {
		frappe.msgprint(__("Save the device before reassigning it."));
		return;
	}
	const expected_user = frm.doc.user;
	frappe.prompt(
		[
			{
				fieldname: "new_user",
				fieldtype: "Link",
				options: "User",
				label: __("New Driver User"),
				reqd: 1,
				get_query: () => ({
					filters: { enabled: 1, name: ["not in", [expected_user, "Guest"]] },
				}),
			},
			{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 },
			{
				fieldname: "local_data_cleared",
				fieldtype: "Check",
				label: __("Pending work is synced and local app data has been cleared"),
				default: 0,
				reqd: 1,
			},
			{
				fieldname: "notice",
				fieldtype: "HTML",
				options: `<p class="text-muted">${__(
					"The new driver's other active device will be revoked. This action cannot remotely erase data stored on the phone."
				)}</p>`,
			},
		],
		async (values) => {
			if (!values.local_data_cleared) {
				frappe.msgprint(
					__("Sync pending work and clear the phone's app data before reassigning it.")
				);
				return;
			}
			await frappe.call({
				method: "vuna_transport.vuna_transport.doctype.transport_mobile_device.transport_mobile_device.reassign_mobile_device",
				args: { device_name: frm.doc.name, expected_user, ...values },
				freeze: true,
			});
			await frm.reload_doc();
			frappe.show_alert({
				message: __("Device reassigned. The new driver can now sign in."),
				indicator: "green",
			});
		},
		__("Reassign Device"),
		__("Reassign and Activate")
	);
}
