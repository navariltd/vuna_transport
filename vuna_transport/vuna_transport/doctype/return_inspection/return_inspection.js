// Copyright (c) 2026, Yovuna Technologies Limited and contributors

frappe.ui.form.on("Return Inspection", {
	async onload(frm) {
		if (!frm.is_new() || frm._inspection_default_doc === frm.doc) return;
		const initial_doc = frm.doc;
		frm._inspection_default_doc = initial_doc;
		const initial_value = frm.doc.inspection_required;
		const value = await frappe.db.get_single_value(
			"Transport Settings",
			"require_quality_inspection_for_return_inspection"
		);
		if (
			frm.is_new() &&
			frm.doc === initial_doc &&
			frm.doc.inspection_required === initial_value
		)
			await frm.set_value("inspection_required", cint(value));
	},
	setup(frm) {
		frm.set_query("quality_inspection", "results", (doc, cdt, cdn) => {
			const row = frappe.get_doc(cdt, cdn);
			return {
				filters: {
					docstatus: ["<", 2],
					reference_type: frm.doc.receiving_stock_entry
						? "Stock Entry"
						: "Delivery Note",
					reference_name: frm.doc.receiving_stock_entry || frm.doc.sales_return,
					item_code: row.item_code,
					child_row_reference: row.stock_entry_detail || row.delivery_note_item,
				},
			};
		});
		frm.set_query("item_code", "results", () => ({
			query: frm.doc.receiving_stock_entry
				? "vuna_transport.services.execution.depot_return_inspection_service.item_query"
				: "vuna_transport.services.execution.return_v2_service.get_sales_return_inspection_item_query",
			filters: {
				sales_return: frm.doc.sales_return,
				receiving_stock_entry: frm.doc.receiving_stock_entry,
				return_request: frm.doc.return_request,
			},
		}));
		frm.set_query("batch_no", "results", (doc, cdt, cdn) => {
			const row = frappe.get_doc(cdt, cdn);
			return {
				query: frm.doc.receiving_stock_entry
					? "vuna_transport.services.execution.depot_return_inspection_service.item_query"
					: "vuna_transport.services.execution.return_v2_service.get_sales_return_inspection_batch_query",
				filters: {
					sales_return: frm.doc.sales_return,
					receiving_stock_entry: frm.doc.receiving_stock_entry,
					return_request: frm.doc.return_request,
					item_code: row.item_code,
				},
			};
		});
		frm.set_query("target_warehouse", "results", () => ({
			filters: { company: frm.doc.company, is_group: 0, disabled: 0 },
		}));
		frm.set_query("accepted_warehouse", () => ({
			filters: { company: frm.doc.company, is_group: 0, disabled: 0 },
		}));
		frm.set_query("rejected_warehouse", () => ({
			filters: {
				company: frm.doc.company,
				is_group: 0,
				disabled: 0,
				is_rejected_warehouse: frm.doc.receiving_stock_entry ? 0 : 1,
			},
		}));
	},

	refresh(frm) {
		render_inspection_uom_summary(frm);
		frm.toggle_display("sales_return", !frm.doc.receiving_stock_entry);
		if (
			!frm.is_new() &&
			frm.doc.docstatus === 0 &&
			frm.has_perm("write") &&
			frappe.model.can_create("Quality Inspection")
		) {
			frm.add_custom_button(
				__("Quality Inspections"),
				() => create_return_quality_inspections(frm),
				__("Create")
			);
			frm.page.set_inner_btn_group_as_primary(__("Create"));
		}
		if (frm.doc.docstatus !== 1 || frm.doc.stock_entry) return;
		frm.add_custom_button(
			__("Stock Entry (Material Transfer)"),
			() => {
				frappe.call({
					method: "vuna_transport.services.execution.return_v2_service.create_return_stock_entry",
					args: { return_inspection: frm.doc.name },
					freeze: true,
					callback: ({ message }) => {
						if (message) frappe.set_route("Form", "Stock Entry", message);
					},
				});
			},
			__("Create")
		);
		frm.page.set_inner_btn_group_as_primary(__("Create"));
	},

	validate(frm) {
		render_inspection_uom_summary(frm);
		// Depot receipts are validated per source row AND batch on the server.
		if (frm.doc.receiving_stock_entry || frm.doc.docstatus === 0) return;
		const totals = {};
		for (const row of frm.doc.results || []) {
			if (!row.delivery_note_item) continue;
			totals[row.delivery_note_item] =
				(totals[row.delivery_note_item] || 0) + flt(row.inspection_qty);
		}
		const mismatches = (frm.doc.results || [])
			.filter((row) => row.delivery_note_item && row.received_stock_qty != null)
			.filter(
				(row, index, rows) =>
					rows.findIndex(
						(candidate) => candidate.delivery_note_item === row.delivery_note_item
					) === index
			)
			.filter(
				(row) =>
					Math.abs((totals[row.delivery_note_item] || 0) - flt(row.received_stock_qty)) >
					1e-6
			);
		if (mismatches.length) {
			const item_names = mismatches
				.map((row) => row.item_code || row.delivery_note_item)
				.join(", ");
			const totals_text = mismatches
				.map((row) => totals[row.delivery_note_item] || 0)
				.join(", ");
			const received_text = mismatches.map((row) => row.received_stock_qty).join(", ");
			frappe.throw(
				__("Inspection rows for") +
					` ${item_names} ` +
					__("total") +
					` ${totals_text}, ` +
					__("but the received quantity is") +
					` ${received_text}.`
			);
		}
	},

	before_submit(frm) {
		const missing_warehouses = (frm.doc.results || []).filter((row) => !row.target_warehouse);
		if (missing_warehouses.length) {
			frappe.validated = false;
			frappe.msgprint({
				title: __("Missing Target Warehouse"),
				message: __(
					"Select a Target Warehouse for every inspection result row before submitting."
				),
				indicator: "orange",
			});
		}
	},
});

function create_return_quality_inspections(frm) {
	if (frm.is_dirty()) {
		frappe.msgprint(__("Save the Return Inspection before creating Quality Inspections."));
		return;
	}
	const selected = new Set(
		frm.fields_dict.results.grid.get_selected_children().map((row) => row.name)
	);
	const rows = (frm.doc.results || []).filter((row) => !row.quality_inspection);
	if (!rows.length) {
		frappe.msgprint(__("Every result row already has a Quality Inspection."));
		return;
	}
	const name = frm.doc.name;
	const modified = frm.doc.modified;
	const dialog = new frappe.ui.Dialog({
		title: __("Create Quality Inspections"),
		size: "large",
		fields: [
			{
				fieldtype: "Table",
				fieldname: "results",
				label: __("Result Rows"),
				cannot_add_rows: true,
				cannot_delete_rows: true,
				data: rows.map((row) => ({
					result_row: row.name,
					item_code: row.item_code,
					result: row.result,
					inspection_qty: row.inspection_qty,
					stock_uom: row.stock_uom,
					batch_no: row.batch_no,
					__checked: selected.size ? selected.has(row.name) : 1,
				})),
				fields: [
					{ fieldname: "result_row", fieldtype: "Data", hidden: 1 },
					{
						fieldname: "item_code",
						fieldtype: "Link",
						options: "Item",
						label: __("Item"),
						read_only: 1,
						in_list_view: 1,
						columns: 3,
					},
					{
						fieldname: "result",
						fieldtype: "Data",
						label: __("Result"),
						read_only: 1,
						in_list_view: 1,
						columns: 2,
					},
					{
						fieldname: "inspection_qty",
						fieldtype: "Float",
						label: __("Quantity"),
						read_only: 1,
						in_list_view: 1,
						columns: 2,
					},
					{
						fieldname: "stock_uom",
						fieldtype: "Link",
						options: "UOM",
						label: __("UOM"),
						read_only: 1,
						in_list_view: 1,
						columns: 1,
					},
					{
						fieldname: "batch_no",
						fieldtype: "Link",
						options: "Batch",
						label: __("Batch"),
						read_only: 1,
						in_list_view: 1,
						columns: 2,
					},
				],
			},
		],
		primary_action_label: __("Create"),
		async primary_action() {
			if (frm.is_dirty() || frm.doc.name !== name || frm.doc.modified !== modified) {
				frappe.msgprint(
					__("The Return Inspection has changed. Save it and reopen this dialog.")
				);
				return;
			}
			const names = dialog.fields_dict.results.grid
				.get_selected_children()
				.map((row) => row.result_row);
			if (!names.length) {
				frappe.msgprint(__("Select at least one result row."));
				return;
			}
			dialog.get_primary_btn().prop("disabled", true);
			try {
				const { message } = await frappe.call({
					method: "vuna_transport.services.execution.return_inspection_quality_service.create_quality_inspections",
					args: { return_inspection: name, result_rows: names, modified },
					freeze: true,
				});
				dialog.hide();
				await frm.reload_doc();
				frappe.show_alert({
					message: __("Created {0} Quality Inspection(s).", [message.created.length]),
					indicator: "green",
				});
			} finally {
				dialog.get_primary_btn().prop("disabled", false);
			}
		},
	});
	dialog.show();
}

frappe.ui.form.on("Return Inspection Result", {
	inspection_qty: render_inspection_uom_summary,
	stock_uom: render_inspection_uom_summary,
	received_stock_qty: render_inspection_uom_summary,
	results_add: render_inspection_uom_summary,
	results_remove: render_inspection_uom_summary,

	item_code(frm, cdt, cdn) {
		if (frappe.get_doc(cdt, cdn).item_code) resolve_receipt_item(frm, cdt, cdn);
	},

	batch_no(frm, cdt, cdn) {
		resolve_receipt_item(frm, cdt, cdn);
	},

	delivery_note_item(frm, cdt, cdn) {
		const row = frappe.get_doc(cdt, cdn);
		if (!row.delivery_note_item) return;
		frappe.db
			.get_value("Delivery Note Item", row.delivery_note_item, [
				"item_code",
				"stock_uom",
				"stock_qty",
				"batch_no",
				"serial_no",
				"serial_and_batch_bundle",
			])
			.then(({ message }) => {
				if (!message) return;
				frappe.model.set_value(cdt, cdn, "stock_uom", message.stock_uom || null);
				frappe.model.set_value(
					cdt,
					cdn,
					"received_stock_qty",
					Math.abs(flt(message.stock_qty))
				);
				frappe.model.set_value(cdt, cdn, "batch_no", message.batch_no || null);
				frappe.model.set_value(cdt, cdn, "serial_no", message.serial_no || null);
				frappe.model.set_value(
					cdt,
					cdn,
					"serial_and_batch_bundle",
					message.serial_and_batch_bundle || null
				);
				if (!row.item_code)
					frappe.model.set_value(cdt, cdn, "item_code", message.item_code);
			});
	},

	result(frm, cdt, cdn) {
		render_inspection_uom_summary(frm);
		const row = frappe.get_doc(cdt, cdn);
		frappe.model.set_value(
			cdt,
			cdn,
			"target_warehouse",
			row.result === "Rejected" ? frm.doc.rejected_warehouse : frm.doc.accepted_warehouse
		);
		if (row.result !== "Rejected") frappe.model.set_value(cdt, cdn, "reason", null);
	},
});

frappe.ui.form.on("Return Inspection", {
	accepted_warehouse(frm) {
		set_result_warehouses(frm);
	},

	rejected_warehouse(frm) {
		set_result_warehouses(frm);
	},
});

function set_result_warehouses(frm) {
	for (const row of frm.doc.results || []) {
		const warehouse =
			row.result === "Rejected" ? frm.doc.rejected_warehouse : frm.doc.accepted_warehouse;
		if (warehouse)
			frappe.model.set_value(row.doctype, row.name, "target_warehouse", warehouse);
	}
}

function resolve_receipt_item(frm, cdt, cdn) {
	const row = frappe.get_doc(cdt, cdn);
	const selected_item = row.item_code;
	const selected_batch = row.batch_no;
	frappe
		.call({
			method: frm.doc.receiving_stock_entry
				? "vuna_transport.services.execution.depot_return_inspection_service.get_item_options"
				: "vuna_transport.services.execution.return_v2_service.get_sales_return_inspection_item_options",
			args: {
				sales_return: frm.doc.sales_return,
				receiving_stock_entry: frm.doc.receiving_stock_entry,
				return_request: frm.doc.return_request,
				item_code: row.item_code,
				batch_no: row.batch_no,
			},
		})
		.then(({ message: matches }) => {
			if (row.item_code !== selected_item || row.batch_no !== selected_batch) return;
			matches = matches || [];
			if (matches.length === 1) {
				frappe.model.set_value(
					cdt,
					cdn,
					frm.doc.receiving_stock_entry ? "stock_entry_detail" : "delivery_note_item",
					matches[0].name
				);
				if (frm.doc.receiving_stock_entry)
					frappe.model.set_value(
						cdt,
						cdn,
						"return_request_item",
						matches[0].return_request_item
					);
				apply_receipt_item_details(cdt, cdn, matches[0]);
			} else if (matches.length > 1) {
				if (frm.doc.receiving_stock_entry) {
					frappe.prompt(
						{
							fieldname: "source",
							fieldtype: "Select",
							label: __("Received Item Group"),
							reqd: 1,
							options: matches.map((item, index) => ({
								value: String(index),
								label: `${item.item_code} · ${
									item.batch_no || __("No batch")
								} · ${__("Row")} ${item.source_row_idx} · ${item.stock_qty} ${
									item.stock_uom
								}`,
							})),
						},
						({ source }) => {
							if (row.item_code !== selected_item || row.batch_no !== selected_batch)
								return;
							const item = matches[Number(source)];
							frappe.model.set_value(cdt, cdn, "stock_entry_detail", item.name);
							frappe.model.set_value(
								cdt,
								cdn,
								"return_request_item",
								item.return_request_item
							);
							apply_receipt_item_details(cdt, cdn, item);
						}
					);
					return;
				}
				frappe.model.set_value(
					cdt,
					cdn,
					frm.doc.receiving_stock_entry ? "stock_entry_detail" : "delivery_note_item",
					null
				);
				frappe.msgprint(__("Select the batch for this item before continuing."));
			} else {
				frappe.model.set_value(
					cdt,
					cdn,
					frm.doc.receiving_stock_entry ? "stock_entry_detail" : "delivery_note_item",
					null
				);
				frappe.msgprint(
					__("Select an item and batch included on the receiving document.")
				);
			}
		});
}

function apply_receipt_item_details(cdt, cdn, item) {
	frappe.model.set_value(cdt, cdn, "stock_uom", item.stock_uom || null);
	frappe.model.set_value(cdt, cdn, "received_stock_qty", Math.abs(flt(item.stock_qty)));
	frappe.model.set_value(cdt, cdn, "batch_no", item.batch_no || null);
	frappe.model.set_value(cdt, cdn, "serial_no", item.serial_no || null);
	frappe.model.set_value(
		cdt,
		cdn,
		"serial_and_batch_bundle",
		item.serial_and_batch_bundle || null
	);
}

function inspection_quantities_by_uom(results) {
	const groups = new Map();
	const number = (value) => (Number.isFinite(Number(value)) ? Number(value) : 0);
	for (const row of results || []) {
		if (!row.stock_uom) continue;
		if (!groups.has(row.stock_uom)) {
			groups.set(row.stock_uom, {
				uom: row.stock_uom,
				sources: new Map(),
				accepted: 0,
				rejected: 0,
			});
		}
		const group = groups.get(row.stock_uom);
		const source = row.stock_entry_detail || row.delivery_note_item;
		if (source) {
			// Split result rows repeat their source/batch received quantity.
			const key = JSON.stringify([
				row.stock_entry_detail ? "stock" : "delivery",
				source,
				row.stock_entry_detail ? row.batch_no || "" : "",
			]);
			group.sources.set(
				key,
				Math.max(group.sources.get(key) || 0, number(row.received_stock_qty))
			);
		}
		if (row.result === "Accepted") group.accepted += number(row.inspection_qty);
		if (row.result === "Rejected") group.rejected += number(row.inspection_qty);
	}
	return Array.from(groups.values()).map((group) => {
		const received = Array.from(group.sources.values()).reduce((total, qty) => total + qty, 0);
		const inspected = group.accepted + group.rejected;
		return {
			uom: group.uom,
			received,
			inspected,
			accepted: group.accepted,
			rejected: group.rejected,
			pending: Math.max(received - inspected, 0),
		};
	});
}

function render_inspection_uom_summary(frm) {
	const groups = inspection_quantities_by_uom(frm.doc.results);
	const mixed = groups.length > 1;
	const labels = {
		total_received_stock_qty: "Received",
		total_inspected_stock_qty: "Inspected",
		accepted_stock_qty: "Accepted",
		rejected_stock_qty: "Rejected",
		pending_stock_qty: "Pending Inspection",
	};
	for (const [field, label] of Object.entries(labels)) {
		frm.toggle_display(field, !mixed);
		frm.set_df_property(
			field,
			"label",
			groups.length === 1 ? __("{0} ({1})", [__(label), groups[0].uom]) : __(label)
		);
	}
	frm.toggle_display("uom_summary", mixed);
	const wrapper = frm.get_field("uom_summary")?.$wrapper;
	if (!wrapper) return;
	if (!mixed) {
		wrapper.empty();
		return;
	}
	const escape = frappe.utils.escape_html;
	const headers = [
		"Stock UOM",
		"Received",
		"Inspected",
		"Accepted",
		"Rejected",
		"Pending Inspection",
	];
	const rows = groups
		.map(
			(group) =>
				`<tr><td>${escape(group.uom)}</td>${[
					group.received,
					group.inspected,
					group.accepted,
					group.rejected,
					group.pending,
				]
					.map(
						(qty) =>
							`<td class="text-right">${frappe.format(qty, {
								fieldtype: "Float",
							})}</td>`
					)
					.join("")}</tr>`
		)
		.join("");
	wrapper.html(
		`<div class="table-responsive"><table class="table table-bordered table-condensed"><thead><tr>${headers
			.map((label) => `<th>${escape(__(label))}</th>`)
			.join("")}</tr></thead><tbody>${rows}</tbody></table></div>`
	);
}
