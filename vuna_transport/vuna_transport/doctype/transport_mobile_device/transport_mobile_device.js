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
