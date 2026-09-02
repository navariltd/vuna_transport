// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Delivery Order", {
	refresh(frm) {
		if (frm.doc.docstatus === 1 && ["Open", "Partially Allocated"].includes(frm.doc.status)) {
			const button = frm.add_custom_button(__("Create"), () => {
				frm.call("create_load").then((response) => {
					if (response.message) {
						frappe.show_alert({
							message: __("Delivery Load {0} created", [response.message]),
							indicator: "green",
						});
						frappe.set_route("Form", "Delivery Load", response.message);
					}
				});
			});
			button.addClass("btn-primary");
		}
	},
});
