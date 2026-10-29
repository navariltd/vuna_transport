// Copyright (c) 2026, Yovuna Technologies Limited and contributors

frappe.ui.form.on("Return Inspection", {
	setup(frm) {
		frm.set_query("item_code", "results", () => ({
			query: "vuna_transport.services.execution.return_v2_service.get_sales_return_inspection_item_query",
			filters: { sales_return: frm.doc.sales_return },
		}));
		frm.set_query("batch_no", "results", (doc, cdt, cdn) => {
			const row = frappe.get_doc(cdt, cdn);
			return {
				query: "vuna_transport.services.execution.return_v2_service.get_sales_return_inspection_batch_query",
				filters: { sales_return: frm.doc.sales_return, item_code: row.item_code },
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
				is_rejected_warehouse: 1,
			},
		}));
	},

	refresh(frm) {
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
	frappe
		.call({
			method: "vuna_transport.services.execution.return_v2_service.get_sales_return_inspection_item_options",
			args: {
				sales_return: frm.doc.sales_return,
				item_code: row.item_code,
				batch_no: row.batch_no,
			},
		})
		.then(({ message: matches }) => {
			matches = matches || [];
			if (matches.length === 1) {
				frappe.model.set_value(cdt, cdn, "delivery_note_item", matches[0].name);
				apply_receipt_item_details(cdt, cdn, matches[0]);
			} else if (matches.length > 1) {
				frappe.model.set_value(cdt, cdn, "delivery_note_item", null);
				frappe.msgprint(__("Select the batch for this item before continuing."));
			} else {
				frappe.model.set_value(cdt, cdn, "delivery_note_item", null);
				frappe.msgprint(__("Select an item included on this Sales Return."));
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

