frappe.ui.form.on("Purchase Receipt", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1 || !frm.doc.vuna_pickup_purchase_order || frm.doc.is_return)
			return;
		frm.add_custom_button(
			__("Stock Entry (Material Transfer)"),
			() => {
				const planned = [
					...new Set(
						(frm.doc.items || [])
							.map((row) => row.vuna_pickup_destination_warehouse)
							.filter(Boolean)
					),
				];
				frappe.prompt(
					{
						fieldname: "warehouse",
						fieldtype: "Link",
						options: "Warehouse",
						label: __("Receiving Warehouse"),
						reqd: 1,
						default: planned.length === 1 ? planned[0] : undefined,
						get_query: () => ({
							filters: {
								company: frm.doc.company,
								disabled: 0,
								is_group: 0,
								warehouse_type: ["!=", "Transit"],
							},
						}),
					},
					async ({ warehouse }) => {
						const { message } = await frappe.call({
							method: "vuna_transport.services.execution.supplier_vehicle_transfer_service.create_vehicle_material_transfer",
							args: { source_name: frm.doc.name, target_warehouse: warehouse },
							freeze: true,
						});
						if (message) frappe.set_route("Form", "Stock Entry", message);
					},
					__("Transfer Vehicle Stock"),
					__("Create Draft")
				);
			},
			__("Create")
		);
	},
});
