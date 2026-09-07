// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Proof of Delivery", {
	refresh(frm) {
		// A Delivery Trip is the execution parent. Cancelling a POD must not
		// offer to cancel the submitted trip; the server-side on_cancel handler
		// resets only this POD's Delivery Stop outcome.
		frm.ignore_doctypes_on_cancel_all = ["Delivery Trip"];
	},

	delivery_stop(frm) {
		if (!frm.doc.delivery_stop) return;

		frappe.db.get_doc("Delivery Stop", frm.doc.delivery_stop).then((stop) => {
			frm.set_value("delivery_trip", stop.parent);
			frm.set_value("delivery_order", stop.vuna_delivery_order);
			frm.set_value("customer", stop.customer);
			frm.set_value("delivery_address", stop.address);
			frm.set_value("delivery_note", stop.delivery_note);
		});
	},
});

frappe.ui.form.on("Proof of Delivery Item", {
	ordered_qty(frm, cdt, cdn) {
		set_rejected_quantity(frm, cdt, cdn);
	},
	delivered_qty(frm, cdt, cdn) {
		set_rejected_quantity(frm, cdt, cdn);
	},
});

function set_rejected_quantity(frm, cdt, cdn) {
	const row = frappe.get_doc(cdt, cdn);
	const rejected = Math.max((row.ordered_qty || 0) - (row.delivered_qty || 0), 0);
	frappe.model.set_value(cdt, cdn, "rejected_qty", rejected);
}
