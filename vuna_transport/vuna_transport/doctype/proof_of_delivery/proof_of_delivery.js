// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Proof of Delivery", {
	setup(frm) {
		frm.set_query("item_code", "items", () =>
			frm.doc.proof_type === "Unplanned Collection"
				? {
						filters: {
							name: [
								"in",
								(frm._collection_source_items || []).map((row) => row.name),
							],
						},
				  }
				: {}
		);
	},
	async collection_reference_doctype(frm) {
		if (frm.doc.proof_type !== "Unplanned Collection") return;
		if (frm.doc.collection_reference_name)
			return frm.set_value("collection_reference_name", "");
		return load_collection_reference(frm, true);
	},
	collection_reference_name(frm) {
		return load_collection_reference(frm, true);
	},
	refresh(frm) {
		if (
			!frm.is_new() &&
			frm.doc.docstatus === 0 &&
			["Return Pickup", "Transfer Receipt"].includes(frm.doc.proof_type) &&
			frappe.model.can_create("Stock Entry")
		) {
			frm.add_custom_button(
				__("Stock Entry for Quality Inspection"),
				() =>
					frappe.call({
						method: "vuna_transport.services.execution.return_stock_preparation_service.prepare_stock_entry",
						args: { proof_name: frm.doc.name },
						freeze: true,
						callback: ({ message }) => {
							if (message) frappe.set_route("Form", "Stock Entry", message);
						},
					}),
				__("Create")
			);
		}
		const unplanned = frm.doc.proof_type === "Unplanned Collection";
		const pickup = unplanned || frm.doc.proof_type === "Return Pickup";
		for (const field of [
			"delivery_load",
			"delivery_stop",
			"delivery_note",
			"delivery_order",
			"delivery_address",
			"customer",
		]) {
			frm.toggle_display(
				field,
				!unplanned &&
					!(
						field === "delivery_load" &&
						frm.doc.proof_type === "Return Pickup" &&
						!frm.doc.delivery_load
					)
			);
		}
		frm.set_df_property("notes", "label", unplanned ? __("Collection Reason") : __("Notes"));
		frm.set_df_property(
			"delivery_datetime",
			"label",
			unplanned ? __("Collection Date and Time") : __("Delivery Date and Time")
		);
		frm.fields_dict.items.grid.update_docfield_property("ordered_qty", "hidden", false);
		frm.fields_dict.items.grid.update_docfield_property("uom", "read_only", true);
		if (unplanned) {
			if (frm.doc.docstatus === 0) load_collection_reference(frm);
			frm.set_intro(
				__(
					"Collection evidence only. Dispatch must link a Return Request and arrange stock receipt. No demand, stock movement or credit is created."
				),
				"orange"
			);
			if (frm.doc.docstatus === 1 && frm.doc.collection_party_type === "Depot") {
				if (!frm.doc.return_request) {
					frm.add_custom_button(
						__("Return Request"),
						() =>
							frappe.prompt(
								[
									{
										fieldname: "source_warehouse",
										fieldtype: "Link",
										options: "Warehouse",
										label: __("Original Depot Stock Warehouse"),
										default: frm.doc.collection_warehouse,
										read_only: 1,
										reqd: 1,
										get_query: () => ({
											filters: {
												company: frm.doc.company,
												is_group: 0,
												disabled: 0,
											},
										}),
									},
									{
										fieldname: "return_warehouse",
										fieldtype: "Link",
										options: "Warehouse",
										label: __("Receiving Returns / Quarantine Warehouse"),
										reqd: 1,
										get_query: () => ({
											filters: {
												company: frm.doc.company,
												is_group: 0,
												disabled: 0,
											},
										}),
									},
									{
										fieldname: "notes",
										fieldtype: "Small Text",
										label: __("Review and Receipt Instructions"),
										reqd: 1,
									},
								],
								(values) =>
									frappe.call({
										method: "vuna_transport.services.execution.unplanned_pickup_service.create_depot_case",
										args: { proof_name: frm.doc.name, ...values },
										freeze: true,
										callback: ({ message }) => {
											if (message)
												frappe.set_route(
													"Form",
													"Return Request",
													message
												);
										},
									}),
								__("Review Depot Collection")
							),
						__("Create")
					);
				} else if (frm.doc.collection_review_status === "Reviewed") {
					frm.add_custom_button(
						__("Stock Entry (Material Transfer)"),
						() =>
							frappe.model.open_mapped_doc({
								method: "vuna_transport.services.execution.unplanned_pickup_service.make_collection_stock_entry",
								frm,
								source_name: frm.doc.name,
							}),
						__("Create")
					);
				}
			}
			if (
				frm.doc.docstatus === 1 &&
				frm.doc.collection_review_status === "Pending Review" &&
				frm.perm[0]?.write
			) {
				frm.add_custom_button(__("Review Collection"), () => {
					frappe.prompt(
						[
							{
								fieldname: "return_request",
								fieldtype: "Link",
								options: "Return Request",
								label: __("Return Request"),
								reqd: 1,
								get_query: () => ({
									filters: {
										company: frm.doc.company,
										docstatus: ["!=", 2],
										return_context:
											frm.doc.collection_party_type === "Depot"
												? "Depot"
												: "Customer",
									},
								}),
							},
							{
								fieldname: "notes",
								fieldtype: "Small Text",
								label: __("Review and Stock Receipt Follow-up"),
								reqd: 1,
							},
						],
						(values) =>
							frappe.call({
								method: "vuna_transport.services.execution.unplanned_pickup_service.review_collection",
								args: { proof_name: frm.doc.name, ...values },
								freeze: true,
								callback: () => frm.reload_doc(),
							}),
						__("Review Unplanned Collection")
					);
				});
			}
		}
		for (const field of [
			"rejected_qty",
			"rejected_serial_no",
			"rejection_reason",
			"rejection_reason_remarks",
		]) {
			frm.fields_dict.items.grid.update_docfield_property(field, "hidden", pickup);
		}
		frm.set_df_property(
			"received_by",
			"label",
			pickup ? __("Released By") : __("Received By")
		);
		frm.fields_dict.items.grid.update_docfield_property(
			"delivered_qty",
			"label",
			pickup ? __("Quantity Collected") : __("Accepted Quantity")
		);
		for (const field of [
			"rejected_goods_handling",
			"depot_received_rejected_qty",
			"depot_received_rejected_serial_no",
			"rejected_with_driver_qty",
		]) {
			frm.fields_dict.items.grid.update_docfield_property(
				field,
				"hidden",
				frm.doc.proof_type !== "Transfer Receipt"
			);
		}
		update_pod_totals(frm);
		if (frm.doc.proof_type === "Transfer Receipt" && frm.doc.docstatus === 0) {
			for (const row of frm.doc.items || []) {
				if (flt(row.rejected_qty) > 0 && !row.rejected_goods_handling) {
					update_depot_custody(frm, row.doctype, row.name);
				}
			}
		}
		// A Delivery Trip is the execution parent. Cancelling a POD must not
		// offer to cancel the submitted trip; the server-side on_cancel handler
		// resets only this POD's Delivery Stop outcome.
		frm.ignore_doctypes_on_cancel_all = ["Delivery Trip"];
		if (frm.doc.transport_trip_task || unplanned) {
			// End Transit is posted atomically on submission; view it in Connections.
			return;
		}
		if (
			frm.doc.docstatus === 1 &&
			["Partially Delivered", "Rejected", "Failed"].includes(frm.doc.status)
		) {
			frappe.db
				.get_value(
					"Return Request",
					{ proof_of_delivery: frm.doc.name, docstatus: ["!=", 2] },
					["name", "docstatus"]
				)
				.then(({ message }) => {
					if (message?.name) {
						frm.add_custom_button(
							__("Open Return Request"),
							() => frappe.set_route("Form", "Return Request", message.name),
							__("Resolve")
						);
						return;
					}
					frm.add_custom_button(
						__("Create Return Request"),
						() =>
							frm.call("create_return_request").then(({ message: request }) => {
								if (request) frappe.set_route("Form", "Return Request", request);
							}),
						__("Resolve")
					);
				});
		}
	},

	delivery_stop(frm) {
		if (frm.doc.transport_trip_task) return;
		if (!frm.doc.delivery_stop) return;

		frappe.db.get_doc("Delivery Stop", frm.doc.delivery_stop).then((stop) => {
			frm.set_value("delivery_trip", stop.parent);
			frm.set_value("delivery_order", stop.vuna_delivery_order);
			frm.set_value("customer", stop.customer);
			frm.set_value("delivery_address", stop.address);
			frm.set_value("delivery_note", stop.delivery_note);
		});
	},
});

frappe.ui.form.on("Proof of Delivery Item", {
	item_code(frm, cdt, cdn) {
		if (frm.doc.proof_type !== "Unplanned Collection") return;
		const row = frappe.get_doc(cdt, cdn);
		const options = (frm._collection_source_items || []).filter(
			(option) => option.name === row.item_code
		);
		const apply = (source) =>
			frappe.model.set_value(cdt, cdn, {
				collection_reference_row: source.reference_row,
				item_name: source.item_name,
				uom: source.uom,
				stock_uom: source.stock_uom,
				conversion_factor: source.conversion_factor,
				batch_no: source.batch_no || "",
				serial_no: source.serial_no || "",
				ordered_qty: source.qty,
			});
		if (options.length === 1) return apply(options[0]);
		if (!options.length)
			return frappe.model.set_value(cdt, cdn, {
				item_code: "",
				collection_reference_row: "",
			});
		frappe.prompt(
			[
				{
					fieldname: "source",
					fieldtype: "Select",
					label: __("Reference Row / Batch"),
					reqd: 1,
					options: options.map((source, index) => ({
						value: source.key,
						label: `${index + 1}: ${source.name} — ${
							source.batch_no || __("No Batch")
						} — ${source.qty} ${source.uom}`,
					})),
				},
			],
			({ source }) => {
				if (row.item_code === options[0].name)
					apply(options.find((option) => option.key === source));
			}
		);
	},
	items_add(frm) {
		update_pod_totals(frm);
	},
	ordered_qty(frm, cdt, cdn) {
		set_rejected_quantity(frm, cdt, cdn);
	},
	delivered_qty(frm, cdt, cdn) {
		set_rejected_quantity(frm, cdt, cdn);
	},
	rejected_qty(frm, cdt, cdn) {
		if (frm.doc.transport_trip_task) set_missing_quantity(frm, cdt, cdn);
		update_depot_custody(frm, cdt, cdn);
		update_pod_totals(frm);
	},
	depot_received_rejected_qty(frm, cdt, cdn) {
		update_depot_custody(frm, cdt, cdn);
	},
	rejected_goods_handling(frm, cdt, cdn) {
		update_depot_custody(frm, cdt, cdn);
	},
	rejected_serial_no(frm, cdt, cdn) {
		update_depot_custody(frm, cdt, cdn);
	},
	items_remove(frm) {
		update_pod_totals(frm);
	},
});

async function load_collection_reference(frm, clear_items = false) {
	if (
		frm.doc.proof_type !== "Unplanned Collection" ||
		!frm.doc.delivery_trip ||
		!frm.doc.collection_stop
	)
		return;
	if (clear_items) {
		frm._collection_source_items = [];
		frm.clear_table("items");
		frm.refresh_field("items");
	}
	const key = JSON.stringify([
		frm.doc.delivery_trip,
		frm.doc.collection_stop,
		frm.doc.collection_reference_doctype,
		frm.doc.collection_reference_name,
	]);
	const { message } = await frappe.call({
		method: "vuna_transport.services.execution.unplanned_pickup_service.desk_collection_reference",
		args: {
			trip_name: frm.doc.delivery_trip,
			stop_name: frm.doc.collection_stop,
			reference_doctype: frm.doc.collection_reference_doctype || "",
			reference_name: frm.doc.collection_reference_name || "",
		},
	});
	if (
		key !==
		JSON.stringify([
			frm.doc.delivery_trip,
			frm.doc.collection_stop,
			frm.doc.collection_reference_doctype,
			frm.doc.collection_reference_name,
		])
	)
		return;
	frm._collection_source_items = message.items || [];
	frm.set_query("collection_reference_doctype", () => ({
		filters: { name: ["in", message.reference_types] },
	}));
	frm.set_df_property(
		"collection_reference_doctype",
		"read_only",
		message.reference_types.length === 1
	);
	if (!message.reference_types.includes(frm.doc.collection_reference_doctype)) {
		await frm.set_value("collection_reference_doctype", message.reference_types[0]);
		return;
	}
	frm.set_query("collection_reference_name", () => ({
		filters: {
			company: frm.doc.company,
			docstatus: 1,
			...(frm.doc.collection_reference_doctype === "Delivery Note"
				? { customer: message.customer, is_return: 0 }
				: {}),
		},
	}));
	frm.set_df_property(
		"collection_reference_name",
		"label",
		__(frm.doc.collection_reference_doctype)
	);
	const warehouses = [
		...new Set((message.items || []).map((row) => row.warehouse).filter(Boolean)),
	];
	await frm.set_value({
		collection_party_type:
			message.party_type ||
			(message.reference_types.length === 1
				? message.reference_types[0] === "Delivery Note"
					? "Customer"
					: "Depot"
				: ""),
		collection_customer: message.customer || "",
		collection_warehouse: warehouses.length === 1 ? warehouses[0] : "",
		collection_location: message.location || "",
	});
}

function update_depot_custody(frm, cdt, cdn) {
	if (frm.doc.proof_type !== "Transfer Receipt") return;
	const row = frappe.get_doc(cdt, cdn);
	const rejected = flt(row.rejected_qty);
	const handling =
		row.rejected_goods_handling ||
		(flt(row.depot_received_rejected_qty) === 0
			? "All remain on vehicle"
			: flt(row.depot_received_rejected_qty) === rejected
			? "All received by depot"
			: "Split between depot and vehicle");
	const qty =
		handling === "All received by depot"
			? rejected
			: handling === "All remain on vehicle"
			? 0
			: flt(row.depot_received_rejected_qty);
	return frappe.model.set_value(cdt, cdn, {
		rejected_goods_handling: rejected ? handling : "",
		depot_received_rejected_qty: qty,
		rejected_with_driver_qty: Math.max(rejected - qty, 0),
		depot_received_rejected_serial_no:
			handling === "All received by depot"
				? row.rejected_serial_no || ""
				: handling === "All remain on vehicle"
				? ""
				: row.depot_received_rejected_serial_no || "",
	});
}

function set_rejected_quantity(frm, cdt, cdn) {
	if (frm.doc.proof_type === "Unplanned Collection") {
		update_pod_totals(frm);
		return;
	}
	if (frm.doc.transport_trip_task) {
		set_missing_quantity(frm, cdt, cdn);
		update_pod_totals(frm);
		return;
	}
	const row = frappe.get_doc(cdt, cdn);
	const rejected = Math.max((row.ordered_qty || 0) - (row.delivered_qty || 0), 0);
	frappe.model.set_value(cdt, cdn, "rejected_qty", rejected);
	update_pod_totals(frm);
}

function set_missing_quantity(frm, cdt, cdn) {
	const row = frappe.get_doc(cdt, cdn);
	frappe.model.set_value(
		cdt,
		cdn,
		"missing_qty",
		Math.max(flt(row.ordered_qty) - flt(row.delivered_qty) - flt(row.rejected_qty), 0)
	);
}

function update_pod_totals(frm) {
	const items = frm.doc.items || [];
	frm.set_value(
		"total_accepted_qty",
		items.reduce((total, row) => total + (row.delivered_qty || 0), 0)
	);
	frm.set_value(
		"total_rejected_qty",
		items.reduce((total, row) => total + (row.rejected_qty || 0), 0)
	);
}
