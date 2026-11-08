// Copyright (c) 2026, Yovuna Technologies Limited and contributors

frappe.ui.form.on("Return Inspection", {
	setup(frm) {
		frm.set_query("quality_inspection", "results", (doc, cdt, cdn) => {
			const row = frappe.get_doc(cdt, cdn);
			return {
				filters: {
					docstatus: 1,
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
		if (frm.doc.receiving_stock_entry) return;
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
