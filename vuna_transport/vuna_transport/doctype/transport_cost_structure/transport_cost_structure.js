// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Transport Cost Structure Component", {
	cost_component(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.cost_component) {
			frm.set_value(`${cdt}.${cdn}.cost_nature`, "");
			return;
		}

		frappe.db.get_value("Transport Cost Component", row.cost_component, "default_cost_nature").then((result) => {
			frm.set_value(`${cdt}.${cdn}.cost_nature`, result.message.default_cost_nature || "");
		});
	},
});
