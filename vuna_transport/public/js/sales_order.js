// Copyright (c) 2026, Yovuna Technologies Limited and Contributors
// For license information, please see license.txt

frappe.ui.form.on("Sales Order", {
	setup(frm) {
		frm.set_query("vuna_delivery_method", () => ({
			filters: {
				disabled: 0,
				selling: 1,
			},
		}));
	},
	refresh(frm) {
		if (frm.doc.docstatus !== 1) return;

		frappe
			.call({
				method: "vuna_transport.services.demand.sales_transport_demand_service.get_generation_status",
				args: { sales_order: frm.doc.name },
			})
			.then(({ message }) => {
				if (!message?.can_generate || frm.doc.docstatus !== 1) return;

				frm.add_custom_button(
					__("Transport Demand"),
					() => generate_transport_demand(frm),
					__("Create")
				);
			})
			.catch(() => {});
	},
});

function generate_transport_demand(frm) {
	frappe
		.call({
			method: "vuna_transport.services.demand.sales_transport_demand_service.generate",
			args: { sales_order: frm.doc.name },
			freeze: true,
			freeze_message: __("Generating Transport Demand..."),
		})
		.then(({ message }) => {
			const count = (message || []).length;
			frappe.show_alert(
				{
					message: count
						? __("Transport Demand ready: {0} document(s).", [count])
						: __("No new Transport Demand was required."),
					indicator: count ? "green" : "blue",
				},
				5
			);
			frm.reload_doc();
		});
}
