// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Proof of Delivery", {
	refresh(frm) {
		update_pod_totals(frm);
		// A Delivery Trip is the execution parent. Cancelling a POD must not
		// offer to cancel the submitted trip; the server-side on_cancel handler
		// resets only this POD's Delivery Stop outcome.
		frm.ignore_doctypes_on_cancel_all = ["Delivery Trip"];
		if (
			frm.doc.docstatus === 1 &&
			["Partially Delivered", "Rejected", "Failed"].includes(frm.doc.status)
		) {
			frappe.db
				.get_value(
					"Return Request",
					{ proof_of_delivery: frm.doc.name, docstatus: ["!=", 2] },
					["name", "docstatus"]
				)
				.then(({ message }) => {
					if (message?.name) return;
					frm.add_custom_button(
						__("Create Return Request"),
						() =>
							frm.call("create_return_request").then(({ message: request }) => {
								if (request) frappe.set_route("Form", "Return Request", request);
							}),
						__("Resolve")
					);
				});
		}
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
	items_add(frm) {
		update_pod_totals(frm);
	},
	ordered_qty(frm, cdt, cdn) {
		set_rejected_quantity(frm, cdt, cdn);
	},
	delivered_qty(frm, cdt, cdn) {
		set_rejected_quantity(frm, cdt, cdn);
	},
	rejected_qty(frm) {
		update_pod_totals(frm);
	},
	items_remove(frm) {
		update_pod_totals(frm);
	},
});

function set_rejected_quantity(frm, cdt, cdn) {
	const row = frappe.get_doc(cdt, cdn);
	const rejected = Math.max((row.ordered_qty || 0) - (row.delivered_qty || 0), 0);
	frappe.model.set_value(cdt, cdn, "rejected_qty", rejected);
	update_pod_totals(frm);
}

function update_pod_totals(frm) {
	const items = frm.doc.items || [];
	frm.set_value(
		"total_accepted_qty",
		items.reduce((total, row) => total + (row.delivered_qty || 0), 0)
	);
	frm.set_value(
		"total_rejected_qty",
		items.reduce((total, row) => total + (row.rejected_qty || 0), 0)
	);
}
