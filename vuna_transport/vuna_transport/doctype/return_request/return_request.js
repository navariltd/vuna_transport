// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

function update_pickup_assignment_action(dialog) {
	const selected = dialog.get_value("delivery_stop");
	const ready =
		!dialog._pickup_stops_loading &&
		dialog.get_value("delivery_trip") &&
		(dialog._pickup_stop_options || []).some((stop) => stop.value === selected);
	if (ready) dialog.enable_primary_action();
	else dialog.disable_primary_action();
}

async function load_pickup_assignment_stops(dialog, request_name) {
	const trip = dialog.get_value("delivery_trip");
	const version = (dialog._pickup_stop_version || 0) + 1;
	dialog._pickup_stop_version = version;
	dialog._pickup_stops_loading = true;
	dialog._pickup_stop_options = [];
	dialog.disable_primary_action();
	const current = () =>
		dialog._pickup_stop_version === version && dialog.get_value("delivery_trip") === trip;
	const set_stop = (value) => {
		// Frappe dialog setters are asynchronous. Serialize writes so an old
		// field reset cannot erase a newer trip's automatically selected stop.
		const write = async () => {
			if (current()) await dialog.set_value("delivery_stop", value);
		};
		dialog._pickup_stop_write = (dialog._pickup_stop_write || Promise.resolve()).then(
			write,
			write
		);
		return dialog._pickup_stop_write;
	};
	try {
		await set_stop("");
		if (!current()) return;
		dialog.set_df_property("delivery_stop", "options", [""]);
		dialog.set_df_property(
			"delivery_stop",
			"description",
			trip ? __("Loading matching pickup stops…") : __("Select a Delivery Trip first.")
		);
		if (!trip) return;
		const { message } = await frappe.call({
			method: "vuna_transport.services.execution.direct_return_pickup_service.get_assignment_stops",
			args: { return_request: request_name, delivery_trip: trip },
		});
		if (!current()) return;
		const stops = message || [];
		dialog._pickup_stop_options = stops;
		// A blank option prevents an apparent DOM selection with no model value.
		dialog.set_df_property("delivery_stop", "options", ["", ...stops]);
		dialog.set_df_property(
			"delivery_stop",
			"description",
			stops.length
				? __("Choose the matching pickup stop.")
				: __("This trip has no unfinished stop matching this return's depot or customer.")
		);
		if (stops.length === 1) await set_stop(stops[0].value);
	} catch {
		if (current())
			dialog.set_df_property(
				"delivery_stop",
				"description",
				__("Could not load pickup stops. Reselect the Delivery Trip to retry.")
			);
	} finally {
		if (current()) {
			dialog._pickup_stops_loading = false;
			update_pickup_assignment_action(dialog);
		}
	}
}

async function select_depot_return_items(frm) {
	if (!frm.doc.source_stock_entry || !frm.doc.source_warehouse) {
		frappe.msgprint(__("Select the depot warehouse and its incoming Stock Entry first."));
		return;
	}
	const { message: items } = await frappe.call({
		method: "vuna_transport.services.execution.manual_depot_return_service.get_depot_return_items",
		args: {
			source_stock_entry: frm.doc.source_stock_entry,
			source_warehouse: frm.doc.source_warehouse,
		},
	});
	const dialog = new frappe.ui.Dialog({
		title: __("Select Received Items to Return"),
		size: "extra-large",
		fields: [
			{
				fieldname: "items",
				fieldtype: "Table",
				label: __("Received Items"),
				cannot_add_rows: true,
				cannot_delete_rows: true,
				data: (items || []).map((row) => ({ ...row, requested_return_qty: 0 })),
				fields: [
					{
						fieldname: "item_code",
						fieldtype: "Data",
						label: __("Item"),
						in_list_view: 1,
						read_only: 1,
					},
					{
						fieldname: "batch_no",
						fieldtype: "Data",
						label: __("Batch"),
						in_list_view: 1,
						read_only: 1,
					},
					{
						fieldname: "delivered_qty",
						fieldtype: "Float",
						label: __("Originally Received"),
						in_list_view: 1,
						read_only: 1,
					},
					{
						fieldname: "uom",
						fieldtype: "Data",
						label: __("UOM"),
						in_list_view: 1,
						read_only: 1,
					},
					{
						fieldname: "requested_return_qty",
						fieldtype: "Float",
						label: __("Return Quantity"),
						in_list_view: 1,
					},
					{
						fieldname: "serial_no",
						fieldtype: "Small Text",
						label: __("Serial Numbers to Return"),
					},
				],
			},
		],
		primary_action_label: __("Use Selected Quantities"),
		primary_action(values) {
			const rows = (values.items || []).filter((row) => flt(row.requested_return_qty) > 0);
			if (!rows.length) {
				frappe.msgprint(__("Enter at least one return quantity."));
				return;
			}
			frm.clear_table("items");
			for (const row of rows) {
				const source = items.find(
					(item) =>
						item.stock_entry_detail === row.stock_entry_detail &&
						item.batch_no === row.batch_no
				);
				frm.add_child("items", {
					...source,
					requested_return_qty: row.requested_return_qty,
					serial_no: row.serial_no,
				});
			}
			frm.refresh_field("items");
			frm.dirty();
			dialog.hide();
		},
	});
	dialog.show();
}

frappe.ui.form.on("Return Request", {
	onload(frm) {
		frm._return_request_customer = frm.doc.customer;
	},

	setup(frm) {
		frm.set_query("source_warehouse", () => ({
			filters: {
				company: frm.doc.company,
				is_group: 0,
				disabled: 0,
				warehouse_type: ["!=", "Transit"],
			},
		}));
		frm.set_query("source_stock_entry", () => ({
			query: "vuna_transport.services.execution.manual_depot_return_service.get_depot_receipt_query",
			filters: { company: frm.doc.company, warehouse: frm.doc.source_warehouse },
		}));
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
		frm.set_query("delivery_method", () => ({
			filters: {
				disabled: 0,
				[frm.doc.return_context === "Depot" ? "internal" : "selling"]: 1,
			},
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
		for (const field of [
			"customer",
			"delivery_note",
			"sales_invoice",
			"sales_order",
			"credit_note",
			"return_stock_document",
		])
			frm.toggle_display(field, frm.doc.return_context !== "Depot");
		if (
			frm.doc.return_context === "Depot" &&
			!frm.doc.proof_of_delivery &&
			frm.doc.docstatus === 0
		) {
			frm.add_custom_button(
				__("Get Received Items"),
				() => select_depot_return_items(frm),
				__("Actions")
			);
		}
		if (
			frm.doc.docstatus === 1 &&
			frm.perm?.[0]?.write &&
			frm.doc.transport_arranged_by === "Company" &&
			!["Closed", "Cancelled", "Resolved"].includes(frm.doc.status)
		) {
			frm.add_custom_button(
				__("Assign Pickup to Delivery Trip"),
				() => {
					const dialog = new frappe.ui.Dialog({
						title: __("Assign Approved Return Pickup"),
						fields: [
							{
								fieldname: "delivery_trip",
								fieldtype: "Link",
								options: "Delivery Trip",
								label: __("Delivery Trip"),
								reqd: 1,
								get_query: () => ({
									query: "vuna_transport.services.execution.direct_return_pickup_service.get_assignment_trip_query",
									filters: { return_request: frm.doc.name },
								}),
								onchange: () => load_pickup_assignment_stops(dialog, frm.doc.name),
							},
							{
								fieldname: "delivery_stop",
								fieldtype: "Select",
								label: __("Pickup Stop"),
								reqd: 1,
								options: [],
								description: __("Select a Delivery Trip first."),
								onchange: () => update_pickup_assignment_action(dialog),
							},
						],
						primary_action_label: __("Assign Pickup"),
						primary_action: (values) =>
							frappe.call({
								method: "vuna_transport.services.execution.direct_return_pickup_service.assign_return_pickup",
								args: { return_request: frm.doc.name, ...values },
								freeze: true,
								callback: ({ message }) => {
									dialog.hide();
									if (message)
										frappe.set_route("Form", "Transport Trip Task", message);
								},
							}),
					});
					dialog.show();
					dialog.disable_primary_action();
				},
				__("Actions")
			);
		}
		if (frm.doc.return_context === "Depot") {
			[
				"customer",
				"delivery_note",
				"sales_invoice",
				"sales_order",
				"credit_note",
				"return_stock_document",
			].forEach((field) => frm.toggle_display(field, false));
			frm.ignore_doctypes_on_cancel_all = ["Proof of Delivery"];
			if (frm.doc.docstatus === 1) {
				if (
					(frm.doc.items || []).some(
						(row) => row.custody_warehouse && flt(row.remaining_qty) > 0
					) &&
					frm.doc.proof_of_delivery &&
					["Still With Vehicle", "Split Custody"].includes(frm.doc.custody)
				) {
					frm.add_custom_button(
						__("End Transit Stock Entry"),
						() =>
							frappe.call({
								method: "vuna_transport.services.execution.depot_return_receiving_service.create_vehicle_receipt",
								args: { return_request: frm.doc.name },
								freeze: true,
								callback: ({ message }) => {
									if (message) frappe.set_route("Form", "Stock Entry", message);
								},
							}),
						__("Create")
					);
				}
				if (frm.doc.transport_arranged_by === "Company")
					frm.add_custom_button(
						__("Transport Demand"),
						() =>
							frappe
								.call({
									method: "vuna_transport.services.demand.transport_demand_service.create_transport_demands",
									args: {
										source_doctype: "Return Request",
										source_name: frm.doc.name,
									},
									freeze: true,
								})
								.then(({ message }) => {
									if (message?.length === 1)
										frappe.set_route("Form", "Transport Demand", message[0]);
									else if (message?.length)
										frappe.set_route("List", "Transport Demand", {
											name: ["in", message],
										});
								}),
						__("Create")
					);
				frm.page.set_inner_btn_group_as_primary(__("Create"));
			}
			return;
		}
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

	delivery_method(frm) {
		frm.remove_custom_button(__("Assign Pickup to Delivery Trip"), __("Actions"));
		frm.remove_custom_button(__("Transport Demand"), __("Create"));
	},

	return_context(frm) {
		if (frm.doc.docstatus !== 0 || frm.doc.proof_of_delivery) return;
		frm.clear_table("items");
		for (const field of [
			"customer",
			"delivery_note",
			"sales_invoice",
			"source_stock_entry",
			"source_warehouse",
			"delivery_method",
		])
			frm.set_value(field, null);
		frm.refresh();
	},

	source_warehouse(frm) {
		if (!frm.doc.proof_of_delivery && frm.doc.docstatus === 0) {
			frm.set_value("source_stock_entry", null);
			frm.clear_table("items");
			frm.refresh_field("items");
		}
	},

	source_stock_entry(frm) {
		if (!frm.doc.proof_of_delivery && frm.doc.docstatus === 0) {
			frm.clear_table("items");
			frm.refresh_field("items");
		}
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
