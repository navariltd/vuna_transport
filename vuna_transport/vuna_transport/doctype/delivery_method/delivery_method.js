// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Delivery Method", {
	transport_arranged_by(frm) {
		if (!frm.doc.transport_arranged_by) return;
		const required = frm.doc.transport_arranged_by === "Company" ? 1 : 0;
		return frm.set_value({
			requires_transport_planning: required,
			requires_vehicle_allocation: required,
			requires_delivery_trip: required,
		});
	},
});
