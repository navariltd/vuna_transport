// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Return Request", {
	onload(frm) {
		frm._return_request_customer = frm.doc.customer;
	},

	setup(frm) {
		frm.set_query("delivery_note", () => {
			if (!require_customer_before_source_selection(frm))
				return { filters: { name: ["=", ""] } };
			const filters = { docstatus: 1, is_return: 0 };
			filters.customer = frm.doc.customer;
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
				reference_type: frm.doc.delivery_note ? "Delivery Note" : "Sales Invoice",
				reference_name: frm.doc.return_stock_document || frm.doc.credit_note,
				item_code: frappe.get_doc(cdt, cdn).item_code,
			},
		}));
		[
			"sales_invoice",
			"delivery_trip",
			"proof_of_delivery",
			"credit_note",
			"replacement_delivery_reference",
			"replacement_sales_order",
			"refund_payment_entry",
		].forEach((fieldname) => {
			frm.set_query(fieldname, () => {
				if (!require_customer_before_source_selection(frm)) {
					return { filters: { name: ["=", ""] } };
				}
				return get_customer_scoped_query_filters(frm, fieldname);
			});
		});
	},

	refresh(frm) {
		// The POD is historical delivery evidence, not a dependent transaction
		// that should be cancelled together with its Return Request.
		frm.ignore_doctypes_on_cancel_all = [
			...new Set([...(frm.ignore_doctypes_on_cancel_all || []), "Proof of Delivery"]),
		];
		add_get_delivered_items_button(frm);
		if (frm.is_new()) return;
		if (frm.doc.process_version === "Custody and Inspection") {
			add_v2_return_actions(frm);
			return;
		}

		frm.remove_custom_button(__("Create Sales Return"), __("Next Action"));
		frm.remove_custom_button(__("Sales Return"), __("Next Action"));
		if (frm.doc.docstatus === 1 && frm.doc.status === "Awaiting Sales Return") {
			frm.add_custom_button(
				__(frm.doc.delivery_note ? "Sales Return" : "Credit Note"),
				() => prepare_return_stock_document(frm),
				__("Create")
			);
			frm.page.set_inner_btn_group_as_primary(__("Create"));
		}
		if (frm.doc.docstatus === 1 && frm.doc.status === "Awaiting Inspection") {
			if ((frm.doc.items || []).some((row) => !row.quality_inspection)) {
				frm.add_custom_button(
					__("Inspect Return"),
					() =>
						frappe.set_route(
							"Form",
							frm.doc.delivery_note ? "Delivery Note" : "Sales Invoice",
							frm.doc.return_stock_document || frm.doc.credit_note
						),
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
	},

	delivery_note(frm) {
		add_get_delivered_items_button(frm);
	},

	sales_invoice(frm) {
		if (!frm.doc.sales_invoice || frm.doc.delivery_note) return;
		frappe
			.call({
				method: "vuna_transport.services.execution.return_service.get_sales_invoice_return_context",
				args: {
					sales_invoice: frm.doc.sales_invoice,
					exclude_request: frm.is_new() ? null : frm.doc.name,
				},
				freeze: true,
				freeze_message: __("Loading return source..."),
			})
			.then(({ message }) => {
				if (!message) return;
				if (message.delivery_note) {
					set_delivery_note_from_invoice(frm, message.delivery_note);
					return;
				}
				if (message.delivery_notes?.length > 1) {
					show_delivery_note_selector(frm, message.delivery_notes);
					return;
				}
				if (message.items?.length) show_returnable_items_dialog(frm, message.items);
			});
	},

	customer(frm) {
		const previous_customer = frm._return_request_customer;
		frm._return_request_customer = frm.doc.customer;
		frm._customer_required_alert_shown = false;
		if (
			frm.doc.docstatus !== 0 ||
			!previous_customer ||
			previous_customer === frm.doc.customer
		) {
			return;
		}

		clear_customer_dependent_context(frm);
	},
});

function add_v2_return_actions(frm) {
	if (frm.doc.docstatus !== 1) return;
	if (["Awaiting Sales Return", "Awaiting Receipt"].includes(frm.doc.status)) {
		frm.add_custom_button(
			__("Sales Return"),
			() => {
				frappe.call({
					method: "vuna_transport.services.execution.return_service.create_return_stock_document",
					args: { return_request: frm.doc.name },
					freeze: true,
					callback: ({ message }) => {
						if (message)
							frappe.set_route(
								"Form",
								message.doctype || "Delivery Note",
								message.name || message
							);
					},
				});
			},
			__("Create")
		);
		frm.page.set_inner_btn_group_as_primary(__("Create"));
	}
	if (frm.doc.status === "Awaiting Inspection" && frm.doc.return_stock_document) {
		frm.add_custom_button(
			__("Return Inspection"),
			() =>
				frappe.call({
					method: "vuna_transport.services.execution.return_v2_service.create_return_inspection_from_sales_return",
					args: { sales_return: frm.doc.return_stock_document },
					freeze: true,
					callback: ({ message }) => {
						if (message) frappe.set_route("Form", "Return Inspection", message);
					},
				}),
			__("Create")
		);
		frm.page.set_inner_btn_group_as_primary(__("Create"));
	}
}

function set_delivery_note_from_invoice(frm, delivery_note) {
	frm.set_value("delivery_note", delivery_note);
	frappe.show_alert({
		message: __("Delivery Note {0} linked from Sales Invoice", [delivery_note]),
		indicator: "blue",
	});
}

function show_delivery_note_selector(frm, delivery_notes) {
	const dialog = new frappe.ui.Dialog({
		title: __("Select Delivery Note"),
		fields: [
			{
				fieldname: "delivery_note",
				fieldtype: "Select",
				label: __("Delivery Note"),
				options: delivery_notes,
				reqd: 1,
			},
		],
		primary_action_label: __("Use Delivery Note"),
		primary_action({ delivery_note }) {
			set_delivery_note_from_invoice(frm, delivery_note);
			dialog.hide();
		},
	});
	dialog.show();
}

function require_customer_before_source_selection(frm) {
	if (frm.doc.customer) return true;
	if (!frm._customer_required_alert_shown) {
		frm._customer_required_alert_shown = true;
		frappe.msgprint(__("Select a Customer before selecting delivery or return documents."));
	}
	frm.set_focus("customer");
	return false;
}

function get_customer_scoped_query_filters(frm, fieldname) {
	const filters = { company: frm.doc.company };
	if (fieldname !== "delivery_trip") filters.customer = frm.doc.customer;
	if (fieldname === "sales_invoice") {
		filters.docstatus = 1;
		filters.is_return = 0;
	}
	if (fieldname === "credit_note") filters.is_return = 1;
	if (fieldname === "refund_payment_entry") {
		delete filters.customer;
		filters.party_type = "Customer";
		filters.party = frm.doc.customer;
	}
	return { filters };
}

function clear_customer_dependent_context(frm) {
	[
		"delivery_note",
		"sales_order",
		"sales_invoice",
		"delivery_order",
		"delivery_trip",
		"proof_of_delivery",
		"delivery_address",
		"return_remarks",
		"custody",
		"responsible_party",
		"physical_resolution",
		"commercial_resolution",
	].forEach((fieldname) => frm.set_value(fieldname, null));
	frm.set_value("return_source", "Manual");
	frm.clear_table("items");
	frm.refresh_field("items");
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
		(frm.doc.items || []).map((item) => [get_return_item_source_key(item), item])
	);
	const data = items.map((item) => {
		const existing = existing_items.get(get_return_item_source_key(item));
		return {
			delivery_note_item: item.delivery_note_item,
			sales_invoice_item: item.sales_invoice_item,
			delivery_order_item: item.delivery_order_item,
			stock_uom: item.stock_uom,
			conversion_factor: item.conversion_factor,
			batch_no: item.batch_no,
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
					{ fieldtype: "Data", fieldname: "sales_invoice_item", hidden: true },
					{ fieldtype: "Data", fieldname: "delivery_order_item", hidden: true },
					{ fieldtype: "Data", fieldname: "stock_uom", hidden: true },
					{ fieldtype: "Float", fieldname: "conversion_factor", hidden: true },
					{
						fieldtype: "Link",
						fieldname: "batch_no",
						label: __("Batch No"),
						options: "Batch",
						read_only: true,
						in_list_view: true,
					},
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
					sales_invoice_item: item.sales_invoice_item,
					delivery_order_item: item.delivery_order_item,
					item_code: item.item_code,
					item_name: item.item_name,
					delivered_qty: item.delivered_qty,
					requested_return_qty: flt(item.requested_return_qty),
					uom: item.uom,
					stock_uom: item.stock_uom,
					conversion_factor: item.conversion_factor,
					batch_no: item.batch_no,
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

function get_return_item_source_key(item) {
	return item.delivery_note_item || item.sales_invoice_item;
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
			const target = response.message;
			if (target) {
				frappe.set_route("Form", target.doctype || "Delivery Note", target.name || target);
			}
		},
	});
}
