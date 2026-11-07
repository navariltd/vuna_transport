frappe.ui.form.on("Transport Demand", {
	setup(frm) {
		frm.set_query("source_warehouse", () => ({
			filters: { company: frm.doc.company, is_group: 0, disabled: 0 },
		}));
	},
	refresh(frm) {
		if (frm.doc.docstatus === 1 && frm.doc.movement_type === "Supplier Pickup") {
			frm.add_custom_button(__("Assign to Trip"), () => {
				frappe.prompt(
					{
						fieldname: "delivery_trip",
						fieldtype: "Link",
						options: "Delivery Trip",
						label: __("Delivery Trip"),
						reqd: 1,
						get_query: () => ({
							filters: {
								company: frm.doc.company,
								docstatus: 1,
								status: ["not in", ["Completed", "Cancelled"]],
							},
						}),
					},
					async ({ delivery_trip }) => {
						const { message } = await frappe.call({
							method: "vuna_transport.services.execution.direct_supplier_pickup_service.assign_supplier_demand",
							args: { transport_demand: frm.doc.name, delivery_trip },
							freeze: true,
						});
						if (message) frappe.set_route("Form", "Transport Trip Task", message);
					},
					__("Assign Supplier Pickup"),
					__("Assign")
				);
			});
		}
		if (frm.doc.docstatus === 1 && frm.doc.movement_type === "Warehouse Transfer") {
			frm.add_custom_button(
				__("Transport Load"),
				async () => {
					const response = await frm.call("create_load");
					if (response.message)
						frappe.set_route("Form", "Transport Load", response.message);
				},
				__("Create")
			);
		}
	},
});
