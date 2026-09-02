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
