// Copyright (c) 2026, Yovuna Technologies Limited and Contributors
// For license information, please see license.txt

frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1) return;

		frappe
			.call({
				method: "vuna_transport.services.demand.delivery_order_service.get_delivery_order_generation_status",
				args: { sales_order: frm.doc.name },
			})
			.then(({ message }) => {
				if (!message?.can_generate || frm.doc.docstatus !== 1) return;

				frm.add_custom_button(
					__("Delivery Orders"),
					() => generate_delivery_orders(frm),
					__("Create")
				);
			})
			.catch(() => {});
	},
});

function generate_delivery_orders(frm) {
	frappe
		.call({
			method: "vuna_transport.services.demand.delivery_order_service.generate_delivery_orders_for_sales_order",
			args: { sales_order: frm.doc.name },
			freeze: true,
			freeze_message: __("Generating Delivery Orders..."),
		})
		.then(({ message }) => {
			const count = (message || []).length;
			frappe.show_alert(
				{
					message: count
						? __("Created {0} Delivery Order(s).", [count])
						: __("No new Delivery Orders were required."),
					indicator: count ? "green" : "blue",
				},
				5
			);
			frm.reload_doc();
		});
}
