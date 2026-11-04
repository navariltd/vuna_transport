// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Delivery Order", {
	async refresh(frm) {
		frm.remove_custom_button(__("Transport Load"), __("Create"));
		if (frm.doc.docstatus === 1 && ["Open", "Partially Allocated"].includes(frm.doc.status)) {
			const button = frm.add_custom_button(
				__("Transport Load"),
				() => {
					open_delivery_load_item_dialog(frm);
				},
				__("Create")
			);
			button.addClass("btn-primary");
		}
		if (frm.doc.docstatus !== 1 || ["Closed", "Cancelled"].includes(frm.doc.status)) return;
		const policy = await frappe.call({
			method: "vuna_transport.services.demand.source_policy_service.get_transport_source_policy",
			args: { source_type: "Sales Order" },
		});
		if (!policy.message?.enabled || frm.doc.docstatus !== 1) return;
		frm.add_custom_button(
			__("Transport Demand"),
			async () => {
				const response = await frappe.call({
					method: "vuna_transport.services.demand.transport_demand_service.create_transport_demands",
					args: { source_doctype: "Delivery Order", source_name: frm.doc.name },
					freeze: true,
				});
				if (response.message?.length === 1)
					frappe.set_route("Form", "Transport Demand", response.message[0]);
				else
					frappe.set_route("List", "Transport Demand", {
						source_document: frm.doc.name,
					});
			},
			__("Create")
		);
	},
});

function open_delivery_load_item_dialog(frm) {
	frappe
		.call({
			method: "vuna_transport.services.planning.delivery_load_service.get_delivery_order_items",
			args: { delivery_order_names: [frm.doc.name] },
			freeze: true,
			freeze_message: __("Loading available items..."),
		})
		.then(({ message: result }) => {
			const items = result?.items || [];
			if (!items.length) {
				frappe.msgprint(__("This Delivery Order has no unallocated items."));
				return;
			}
			show_delivery_load_item_dialog(items);
		});
}

function show_delivery_load_item_dialog(items) {
	const data = items.map((item) => ({
		delivery_order: item.delivery_order,
		delivery_order_item: item.delivery_order_item,
		item_code: item.item_code,
		item_name: item.item_name,
		available_quantity: flt(item.available_quantity),
		uom: item.uom,
		allocated_quantity: flt(item.available_quantity),
		__checked: 1,
	}));
	const dialog = new frappe.ui.Dialog({
		title: __("Create Transport Load"),
		size: "large",
		fields: [
			{
				fieldtype: "Table",
				fieldname: "items",
				label: __("Items to Bundle"),
				cannot_add_rows: true,
				cannot_delete_rows: true,
				in_place_edit: true,
				data,
				get_data: () => data,
				fields: [
					{ fieldtype: "Data", fieldname: "delivery_order", hidden: true },
					{ fieldtype: "Data", fieldname: "delivery_order_item", hidden: true },
					{
						fieldtype: "Read Only",
						fieldname: "item_code",
						label: __("Item Code"),
						in_list_view: true,
					},
					{
						fieldtype: "Read Only",
						fieldname: "item_name",
						label: __("Item Name"),
						in_list_view: true,
					},
					{
						fieldtype: "Float",
						fieldname: "available_quantity",
						label: __("Available Quantity"),
						read_only: true,
						in_list_view: true,
					},
					{
						fieldtype: "Data",
						fieldname: "uom",
						label: __("UOM"),
						read_only: true,
						in_list_view: true,
					},
					{
						fieldtype: "Float",
						fieldname: "allocated_quantity",
						label: __("Quantity to Load"),
						reqd: true,
						in_list_view: true,
					},
				],
			},
		],
		primary_action_label: __("Create Transport Load"),
		primary_action(values) {
			const selected = dialog.fields_dict.items.grid.get_selected_children();
			const invalid_quantity = selected.some(
				(item) =>
					flt(item.allocated_quantity) <= 0 ||
					flt(item.allocated_quantity) > flt(item.available_quantity)
			);
			if (invalid_quantity) {
				frappe.msgprint(
					__(
						"Each selected quantity must be greater than zero and no more than its available quantity."
					)
				);
				return;
			}
			if (!selected.length) {
				frappe.msgprint(__("Select at least one item to bundle."));
				return;
			}
			const allocations = selected.map((item) => ({
				delivery_order: item.delivery_order,
				delivery_order_item: item.delivery_order_item,
				allocated_quantity: flt(item.allocated_quantity),
			}));

			frappe
				.call({
					method: "vuna_transport.services.planning.delivery_load_service.create_delivery_load_from_plan",
					args: { allocations },
					freeze: true,
					freeze_message: __("Creating Transport Load..."),
				})
				.then(({ message: delivery_load }) => {
					if (!delivery_load) return;
					dialog.hide();
					frappe.show_alert({
						message: __("Transport Load {0} created", [delivery_load]),
						indicator: "green",
					});
					frappe.set_route("Form", "Transport Load", delivery_load);
				});
		},
	});
	dialog.show();
}
