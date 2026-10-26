// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Return Request", {
	setup(frm) {
		frm.set_query("delivery_note", () => {
			const filters = { docstatus: 1, is_return: 0 };
			if (frm.doc.customer) filters.customer = frm.doc.customer;
			if (frm.doc.company) filters.company = frm.doc.company;
			return { filters };
		});
		frm.set_query("return_warehouse", () => ({
			filters: { company: frm.doc.company, is_group: 0, disabled: 0 },
		}));
		frm.set_query("resolution_type", "items", () => ({
			filters: { disabled: 0 },
		}));
		frm.set_query("quality_inspection", "items", (doc, cdt, cdn) => ({
			filters: {
				reference_type: "Delivery Note",
				reference_name: frm.doc.return_stock_document,
				item_code: frappe.get_doc(cdt, cdn).item_code,
			},
		}));
		frm.set_query("credit_note", () => ({
			filters: { customer: frm.doc.customer, company: frm.doc.company, is_return: 1 },
		}));
		frm.set_query("replacement_delivery_reference", () => ({
			filters: { customer: frm.doc.customer, company: frm.doc.company },
		}));
		frm.set_query("replacement_sales_order", () => ({
			filters: { customer: frm.doc.customer, company: frm.doc.company },
		}));
		frm.set_query("refund_payment_entry", () => ({
			filters: { company: frm.doc.company, party_type: "Customer", party: frm.doc.customer },
		}));
	},

	refresh(frm) {
		// The POD is historical delivery evidence, not a dependent transaction
		// that should be cancelled together with its Return Request.
		frm.ignore_doctypes_on_cancel_all = [
			...new Set([...(frm.ignore_doctypes_on_cancel_all || []), "Proof of Delivery"]),
		];
		render_process_summary(frm);
		add_get_delivered_items_button(frm);
		if (frm.is_new()) return;

		frm.remove_custom_button(__("Create Sales Return"), __("Next Action"));
		frm.remove_custom_button(__("Sales Return"), __("Next Action"));
		if (frm.doc.docstatus === 1 && frm.doc.status === "Awaiting Sales Return") {
			frm.add_custom_button(
				__("Sales Return"),
				() => prepare_return_stock_document(frm),
				__("Create")
			);
			frm.page.set_inner_btn_group_as_primary(__("Create"));
		}
		if (frm.doc.docstatus === 1 && frm.doc.status === "Awaiting Inspection") {
			if ((frm.doc.items || []).some((row) => !row.quality_inspection)) {
				frm.add_custom_button(
					__("Inspect Sales Return"),
					() => frappe.set_route("Form", "Delivery Note", frm.doc.return_stock_document),
					__("Next Action")
				);
			}
		}
		if (frm.doc.status === "Resolved") {
			frm.add_custom_button(
				__("Close Return Request"),
				() => transition(frm, "close"),
				__("Actions")
			);
		}
		add_resolution_actions(frm);
	},

	delivery_note(frm) {
		add_get_delivered_items_button(frm);
	},
});

function render_process_summary(frm) {
	const field = frm.fields_dict.process_summary;
	if (!field?.$wrapper) return;
	const escape = frappe.utils.escape_html;
	const expected = flt(frm.doc.expected_return_qty);
	const returned = flt(frm.doc.returned_qty);
	const difference = flt(frm.doc.return_difference_qty);
	field.$wrapper.html(`
		<div class="rounded border p-3 mb-3">
			<div class="text-muted text-uppercase small">${__("Current Stage")}</div>
			<div class="font-weight-bold mb-3">${escape(frm.doc.status || __("Draft"))}</div>
			<div class="d-flex flex-wrap" style="gap: 2rem">
				<div><div class="text-muted small">${__("Expected")}</div><strong>${format_number(
		expected
	)}</strong></div>
				<div><div class="text-muted small">${__("Returned")}</div><strong>${format_number(
		returned
	)}</strong></div>
				<div><div class="text-muted small">${__("Difference")}</div><strong>${format_number(
		difference
	)}</strong></div>
			</div>
		</div>
	`);
}

frappe.ui.form.on("Return Request Item", {
	requested_return_qty(frm, cdt, cdn) {
		const row = frappe.get_doc(cdt, cdn);
		frappe.model.set_value(
			cdt,
			cdn,
			"remaining_qty",
			Math.max(flt(row.requested_return_qty) - flt(row.received_qty), 0)
		);
	},
});

function add_get_delivered_items_button(frm) {
	frm.remove_custom_button(__("Get Delivered Items"), __("Actions"));
	if (frm.doc.docstatus !== 0 || !frm.doc.delivery_note || frm.doc.proof_of_delivery) return;
	frm.add_custom_button(
		__("Get Delivered Items"),
		() => get_delivered_items(frm),
		__("Actions")
	);
}

function get_delivered_items(frm) {
	frappe.call({
		method: "vuna_transport.services.execution.return_service.get_returnable_items",
		args: {
			delivery_note: frm.doc.delivery_note,
			exclude_request: frm.is_new() ? null : frm.doc.name,
		},
		freeze: true,
		callback: (response) => {
			const items = response.message || [];
			if (!items.length) {
				frappe.msgprint(__("This Delivery Note has no remaining returnable items."));
				return;
			}
			show_returnable_items_dialog(frm, items);
		},
	});
}

function show_returnable_items_dialog(frm, items) {
	const existing_items = new Map(
		(frm.doc.items || []).map((item) => [item.delivery_note_item, item])
	);
	const data = items.map((item) => {
		const existing = existing_items.get(item.delivery_note_item);
		return {
			delivery_note_item: item.delivery_note_item,
			delivery_order_item: item.delivery_order_item,
			stock_uom: item.stock_uom,
			conversion_factor: item.conversion_factor,
			item_code: item.item_code,
			item_name: item.item_name,
			delivered_qty: item.delivered_qty,
			available_return_qty: item.requested_return_qty,
			uom: item.uom,
			requested_return_qty: existing
				? flt(existing.requested_return_qty)
				: flt(item.requested_return_qty),
			__checked: existing ? 1 : 0,
		};
	});
	const dialog = new frappe.ui.Dialog({
		title: __("Select Items for Return"),
		size: "extra-large",
		fields: [
			{
				fieldtype: "Table",
				fieldname: "items",
				label: __("Delivered Items"),
				cannot_add_rows: true,
				cannot_delete_rows: true,
				in_place_edit: true,
				data,
				get_data: () => data,
				fields: [
					{ fieldtype: "Data", fieldname: "delivery_note_item", hidden: true },
					{ fieldtype: "Data", fieldname: "delivery_order_item", hidden: true },
					{ fieldtype: "Data", fieldname: "stock_uom", hidden: true },
					{ fieldtype: "Float", fieldname: "conversion_factor", hidden: true },
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
						fieldname: "delivered_qty",
						label: __("Delivered Quantity"),
						read_only: true,
						in_list_view: true,
					},
					{
						fieldtype: "Float",
						fieldname: "available_return_qty",
						label: __("Available to Return"),
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
						fieldname: "requested_return_qty",
						label: __("Requested Return Quantity"),
						reqd: true,
						in_list_view: true,
					},
				],
			},
		],
		primary_action_label: __("Add Selected Items"),
		primary_action(values) {
			const selected = (values.items || []).filter((item) => item?.__checked == 1);
			const invalid_quantity = selected.some(
				(item) =>
					flt(item.requested_return_qty) <= 0 ||
					flt(item.requested_return_qty) > flt(item.available_return_qty)
			);
			if (invalid_quantity) {
				frappe.msgprint(
					__(
						"Each selected return quantity must be greater than zero and no more than its available return quantity."
					)
				);
				return;
			}
			if (!selected.length) {
				frappe.msgprint(__("Select at least one item to return."));
				return;
			}

			frm.clear_table("items");
			selected.forEach((item) => {
				frm.add_child("items", {
					delivery_note_item: item.delivery_note_item,
					delivery_order_item: item.delivery_order_item,
					item_code: item.item_code,
					item_name: item.item_name,
					delivered_qty: item.delivered_qty,
					requested_return_qty: flt(item.requested_return_qty),
					uom: item.uom,
					stock_uom: item.stock_uom,
					conversion_factor: item.conversion_factor,
				});
			});
			frm.refresh_field("items");
			frm.dirty();
			dialog.hide();
			frappe.show_alert({
				message: __("{0} return item(s) added", [selected.length]),
				indicator: "green",
			});
		},
	});
	dialog.show();
}

function transition(frm, action) {
	frappe.call({
		method: "vuna_transport.services.execution.return_service.transition_return_request",
		args: { return_request: frm.doc.name, action },
		freeze: true,
		callback: () => frm.reload_doc(),
	});
}

function prepare_return_stock_document(frm) {
	frappe.call({
		method: "vuna_transport.services.execution.return_service.create_return_stock_document",
		args: { return_request: frm.doc.name },
		freeze: true,
		callback: (response) => {
			if (response.message) {
				frappe.set_route("Form", "Delivery Note", response.message);
			}
		},
	});
}

function add_resolution_actions(frm) {
	if (frm.doc.status !== "Awaiting Commercial Resolution") return;
	if (!frm.doc.replacement_sales_order && frm.doc.sales_order) {
		frm.add_custom_button(
			__("Create Replacement"),
			() => create_replacement_sales_order(frm),
			__("Resolve")
		);
	}
	frm.add_custom_button(
		__("No Further Action"),
		() => record_no_further_action(frm),
		__("Resolve")
	);
}

function record_no_further_action(frm) {
	frappe.prompt(
		[
			{
				fieldname: "remarks",
				fieldtype: "Small Text",
				label: __("Decision Remarks"),
				reqd: 1,
			},
		],
		({ remarks }) => {
			frappe.call({
				method: "vuna_transport.services.execution.return_service.record_no_further_action",
				args: { return_request: frm.doc.name, remarks },
				freeze: true,
				callback: () => frm.reload_doc(),
			});
		},
		__("No Further Action"),
		__("Confirm")
	);
}

function create_replacement_sales_order(frm) {
	frappe.call({
		method: "vuna_transport.services.execution.return_service.create_replacement_sales_order",
		args: { return_request: frm.doc.name },
		freeze: true,
		callback: ({ message }) => {
			if (message) frappe.set_route("Form", "Sales Order", message);
		},
	});
}
