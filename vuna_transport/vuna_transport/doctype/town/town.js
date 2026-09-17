// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Town", {
	setup(frm) {
		frm.set_query("constituency", () => ({
			filters: frm.doc.county
				? { county: frm.doc.county, disabled: 0 }
				: { disabled: 0 },
		}));
	},
	constituency(frm) {
		if (!frm.doc.constituency) return;
		frappe.db.get_value("Constituency", frm.doc.constituency, "county").then((result) => {
			if (result.message?.county && result.message.county !== frm.doc.county) {
				frm.set_value("county", result.message.county);
			}
		});
	},
});
