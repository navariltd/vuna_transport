// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vehicle Availability Block", {
	setup(frm) {
		frm.set_query("vehicle", () => ({
			filters: frm.doc.company ? { company: frm.doc.company } : {},
		}));
	},
});
