frappe.ui.form.on("Transport Demand", {
	setup(frm) {
		frm.set_query("source_warehouse", () => ({
			filters: { company: frm.doc.company, is_group: 0, disabled: 0 },
		}));
	},
	refresh(frm) {
		if (frm.doc.migration_archived) {
			frm.disable_save();
			frm.set_intro(
				__("Historical migration record. This demand cannot create new transport work."),
				"blue"
			);
			return;
		}
		if (
			frm.doc.docstatus === 1 &&
			frm.doc.movement_type === "Customer Delivery" &&
			frm.doc.source_document_type === "Sales Order" &&
			!["Closed", "Cancelled", "Delivered"].includes(frm.doc.status)
		) {
			frm.add_custom_button(__("Transport Load"), () => open_load_dialog(frm), __("Create"));
		}
		if (frm.doc.docstatus === 1 && frm.doc.movement_type === "Supplier Pickup") {
			if (frm.doc.__onload?.supplier_requires_planning) {
				frm.add_custom_button(
					__("Transport Load"),
					() => open_load_dialog(frm),
					__("Create")
				);
			} else
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
		if (
			frm.doc.docstatus === 1 &&
			frm.doc.movement_type === "Warehouse Transfer" &&
			!["Closed", "Cancelled"].includes(frm.doc.status)
		) {
			frm.add_custom_button(__("Transport Load"), () => open_load_dialog(frm), __("Create"));
		}
	},
});

async function open_load_dialog(frm) {
	const { message: items } = await frappe.call({
		method:
			frm.doc.movement_type === "Supplier Pickup"
				? "vuna_transport.services.planning.supplier_load_service.get_load_items"
				: frm.doc.movement_type === "Warehouse Transfer"
				? "vuna_transport.services.planning.transport_load_service.get_transfer_load_items"
				: "vuna_transport.services.planning.customer_demand_load_service.get_load_items",
		args: { transport_demand: frm.doc.name },
		freeze: true,
	});
	if (!items?.length) return frappe.msgprint(__("No unallocated items remain."));
	const data = items.map((row) => ({
		...row,
		allocated_quantity: row.available_quantity,
		__checked: 1,
	}));
	const dialog = new frappe.ui.Dialog({
		title: __("Create Transport Load"),
		size: "large",
		fields: [
			{
				fieldname: "items",
				fieldtype: "Table",
				label: __("Items to Bundle"),
				cannot_add_rows: true,
				cannot_delete_rows: true,
				in_place_edit: true,
				data,
				get_data: () => data,
				fields: [
					{ fieldname: "transport_demand", fieldtype: "Data", hidden: true },
					{ fieldname: "transport_demand_item", fieldtype: "Data", hidden: true },
					{
						fieldname: "item_code",
						fieldtype: "Data",
						label: __("Item"),
						read_only: true,
						in_list_view: true,
					},
					{
						fieldname: "item_name",
						fieldtype: "Data",
						label: __("Item Name"),
						read_only: true,
						in_list_view: true,
					},
					{
						fieldname: "uom",
						fieldtype: "Data",
						label: __("UOM"),
						read_only: true,
						in_list_view: true,
					},
					{
						fieldname: "available_quantity",
						fieldtype: "Float",
						label: __("Available"),
						read_only: true,
						in_list_view: true,
					},
					{
						fieldname: "allocated_quantity",
						fieldtype: "Float",
						label: __("Quantity to Load"),
						reqd: true,
						in_list_view: true,
					},
				],
			},
		],
		primary_action_label: __("Create Transport Load"),
		async primary_action() {
			const selected = dialog.fields_dict.items.grid.get_selected_children();
			if (!selected.length) return frappe.msgprint(__("Select at least one item."));
			if (
				selected.some(
					(r) =>
						!Number.isFinite(Number(r.allocated_quantity)) ||
						flt(r.allocated_quantity) <= 0 ||
						flt(r.allocated_quantity) > flt(r.available_quantity)
				)
			) {
				return frappe.msgprint(
					__("Enter a positive quantity no greater than the available quantity.")
				);
			}
			dialog.disable_primary_action();
			try {
				const { message: load } = await frappe.call({
					method: "vuna_transport.services.planning.transport_load_service.create_transport_load_from_demands",
					args: {
						allocations: selected.map((r) => ({
							transport_demand: r.transport_demand,
							transport_demand_item: r.transport_demand_item,
							allocated_quantity: flt(r.allocated_quantity),
						})),
					},
					freeze: true,
				});
				if (load) {
					dialog.hide();
					frappe.set_route("Form", "Transport Load", load);
				}
			} finally {
				dialog.enable_primary_action();
			}
		},
	});
	dialog.show();
}
