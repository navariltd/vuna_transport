// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Quality Inspection", {
	refresh(frm) {
		// The Return Request records this result but should not be cancelled with
		// the inspection. Server hooks release its item-level backlink first.
		frm.ignore_doctypes_on_cancel_all = [
			...new Set([...(frm.ignore_doctypes_on_cancel_all || []), "Return Request"]),
		];
	},
});
