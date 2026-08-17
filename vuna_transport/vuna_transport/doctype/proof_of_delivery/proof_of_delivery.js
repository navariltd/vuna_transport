// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Proof of Delivery", {
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
