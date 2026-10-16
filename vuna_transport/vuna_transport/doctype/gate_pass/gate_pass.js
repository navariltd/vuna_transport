frappe.ui.form.on("Gate Pass", {
	refresh(frm) {
		if (frm.doc.docstatus === 1 && frm.doc.status === "Issued") {
			frm.add_custom_button(
				__("Mark Gate Out"),
				() => frm.call("mark_gate_out").then(() => frm.reload_doc()),
				__("Gate Control")
			);
		}
		if (frm.doc.docstatus === 1 && frm.doc.status === "Exited") {
			frm.add_custom_button(
				__("Mark Gate In"),
				() => frm.call("mark_gate_in").then(() => frm.reload_doc()),
				__("Gate Control")
			);
		}
		if (frm.doc.delivery_trip) {
			frm.add_custom_button(
				__("Open Delivery Trip"),
				() => {
					frappe.set_route("Form", "Delivery Trip", frm.doc.delivery_trip);
				},
				__("Open")
			);
		}
		if (frm.doc.vehicle_allocation) {
			frm.add_custom_button(
				__("Open Vehicle Allocation"),
				() => {
					frappe.set_route("Form", "Vehicle Allocation", frm.doc.vehicle_allocation);
				},
				__("Open")
			);
		}
		if (frm.doc.delivery_load) {
			frm.add_custom_button(
				__("Open Delivery Load"),
				() => {
					frappe.set_route("Form", "Delivery Load", frm.doc.delivery_load);
				},
				__("Open")
			);
		}
	},
});
