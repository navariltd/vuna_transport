// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Transport Cost Profile", {
	setup(frm) {
		frm.set_query("vehicle", () => ({ filters: { company: frm.doc.company } }));
		frm.set_query("vehicle_category", () => ({ filters: { disabled: 0 } }));
	},
});
