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
		add_view_buttons(frm);
		add_get_delivered_items_button(frm);
		if (frm.is_new()) return;

		if (frm.doc.docstatus === 1 && frm.doc.status === "Awaiting Sales Return") {
			frm.add_custom_button(
				__("Create Sales Return"),
				() => prepare_return_stock_document(frm),
				__("Next Action")
			);
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
	if (!frm.doc.delivery_note) return;
	frm.add_custom_button(
		__("Get Delivered Items"),
		() => get_delivered_items(frm),
		__("Actions")
	);
}

function get_delivered_items(frm) {
	frappe.call({
		method: "vuna_transport.services.execution.return_service.get_returnable_items",
		args: { delivery_note: frm.doc.delivery_note },
		freeze: true,
		callback: (response) => {
			const items = response.message || [];
			frm.clear_table("items");
			items.forEach((item) => frm.add_child("items", item));
			frm.refresh_field("items");
			if (!items.length) {
				frappe.msgprint(__("This Delivery Note has no remaining returnable items."));
				return;
			}
			frm.dirty();
			frappe.show_alert({
				message: __("{0} returnable item(s) added", [items.length]),
				indicator: "green",
			});
		},
	});
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
	if (!frm.doc.credit_note) {
		frm.add_custom_button(
			__("Create Credit Note"),
			() => create_credit_note(frm),
			__("Resolve")
		);
	}
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

function add_view_buttons(frm) {
	[
		["delivery_note", "Delivery Note", __("Original Delivery Note")],
		["sales_order", "Sales Order", __("Sales Order")],
		["sales_invoice", "Sales Invoice", __("Original Sales Invoice")],
		["delivery_trip", "Delivery Trip", __("Delivery Trip")],
		["proof_of_delivery", "Proof of Delivery", __("Proof of Delivery")],
		["return_stock_document", "Delivery Note", __("Return Stock Document")],
		["credit_note", "Sales Invoice", __("Credit Note")],
		["replacement_sales_order", "Sales Order", __("Replacement Sales Order")],
		["refund_payment_entry", "Payment Entry", __("Refund Payment Entry")],
		[
			"replacement_delivery_reference",
			"Delivery Order",
			__("Replacement / Follow-up Delivery"),
		],
	].forEach(([fieldname, doctype, label]) => {
		if (!frm.doc[fieldname]) return;
		frm.add_custom_button(
			label,
			() => frappe.set_route("Form", doctype, frm.doc[fieldname]),
			__("View")
		);
	});
	(frm.doc.items || []).forEach((row) => {
		if (!row.quality_inspection) return;
		frm.add_custom_button(
			__("Quality Inspection: {0}", [row.item_code]),
			() => frappe.set_route("Form", "Quality Inspection", row.quality_inspection),
			__("View")
		);
	});
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

function create_credit_note(frm) {
	frappe.call({
		method: "vuna_transport.services.execution.return_service.create_return_credit_note",
		args: { return_request: frm.doc.name },
		freeze: true,
		callback: ({ message }) => {
			if (message) frappe.set_route("Form", "Sales Invoice", message);
		},
	});
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
