// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Proof of Delivery", {
	refresh(frm) {
		for (const field of [
			"rejected_goods_handling",
			"depot_received_rejected_qty",
			"depot_received_rejected_serial_no",
			"rejected_with_driver_qty",
		]) {
			frm.fields_dict.items.grid.update_docfield_property(
				field,
				"hidden",
				frm.doc.proof_type !== "Transfer Receipt"
			);
		}
		update_pod_totals(frm);
		if (frm.doc.proof_type === "Transfer Receipt" && frm.doc.docstatus === 0) {
			for (const row of frm.doc.items || []) {
				if (flt(row.rejected_qty) > 0 && !row.rejected_goods_handling) {
					update_depot_custody(frm, row.doctype, row.name);
				}
			}
		}
		// A Delivery Trip is the execution parent. Cancelling a POD must not
		// offer to cancel the submitted trip; the server-side on_cancel handler
		// resets only this POD's Delivery Stop outcome.
		frm.ignore_doctypes_on_cancel_all = ["Delivery Trip"];
		if (frm.doc.transport_trip_task) {
			// End Transit is posted atomically on submission; view it in Connections.
			return;
		}
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
					if (message?.name) {
						frm.add_custom_button(
							__("Open Return Request"),
							() => frappe.set_route("Form", "Return Request", message.name),
							__("Resolve")
						);
						return;
					}
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
		if (frm.doc.transport_trip_task) return;
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
	rejected_qty(frm, cdt, cdn) {
		if (frm.doc.transport_trip_task) set_missing_quantity(frm, cdt, cdn);
		update_depot_custody(frm, cdt, cdn);
		update_pod_totals(frm);
	},
	depot_received_rejected_qty(frm, cdt, cdn) {
		update_depot_custody(frm, cdt, cdn);
	},
	rejected_goods_handling(frm, cdt, cdn) {
		update_depot_custody(frm, cdt, cdn);
	},
	rejected_serial_no(frm, cdt, cdn) {
		update_depot_custody(frm, cdt, cdn);
	},
	items_remove(frm) {
		update_pod_totals(frm);
	},
});

function update_depot_custody(frm, cdt, cdn) {
	if (frm.doc.proof_type !== "Transfer Receipt") return;
	const row = frappe.get_doc(cdt, cdn);
	const rejected = flt(row.rejected_qty);
	const handling =
		row.rejected_goods_handling ||
		(flt(row.depot_received_rejected_qty) === 0
			? "All remain on vehicle"
			: flt(row.depot_received_rejected_qty) === rejected
			? "All received by depot"
			: "Split between depot and vehicle");
	const qty =
		handling === "All received by depot"
			? rejected
			: handling === "All remain on vehicle"
			? 0
			: flt(row.depot_received_rejected_qty);
	return frappe.model.set_value(cdt, cdn, {
		rejected_goods_handling: rejected ? handling : "",
		depot_received_rejected_qty: qty,
		rejected_with_driver_qty: Math.max(rejected - qty, 0),
		depot_received_rejected_serial_no:
			handling === "All received by depot"
				? row.rejected_serial_no || ""
				: handling === "All remain on vehicle"
				? ""
				: row.depot_received_rejected_serial_no || "",
	});
}

function set_rejected_quantity(frm, cdt, cdn) {
	if (frm.doc.transport_trip_task) {
		set_missing_quantity(frm, cdt, cdn);
		update_pod_totals(frm);
		return;
	}
	const row = frappe.get_doc(cdt, cdn);
	const rejected = Math.max((row.ordered_qty || 0) - (row.delivered_qty || 0), 0);
	frappe.model.set_value(cdt, cdn, "rejected_qty", rejected);
	update_pod_totals(frm);
}

function set_missing_quantity(frm, cdt, cdn) {
	const row = frappe.get_doc(cdt, cdn);
	frappe.model.set_value(
		cdt,
		cdn,
		"missing_qty",
		Math.max(flt(row.ordered_qty) - flt(row.delivered_qty) - flt(row.rejected_qty), 0)
	);
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
