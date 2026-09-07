// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Return Request", {
	setup(frm) {
		frm.set_query("return_warehouse", () => ({
			filters: { company: frm.doc.company, is_group: 0, disabled: 0 },
		}));
		frm.set_query("resolution_type", "items", () => ({
			filters: { disabled: 0 },
		}));
		frm.set_query("quality_inspection", "items", (doc, cdt, cdn) => ({
			filters: {
				reference_type: "Delivery Note",
				reference_name: frm.doc.delivery_note,
				item_code: frappe.get_doc(cdt, cdn).item_code,
			},
		}));
		frm.set_query("credit_note", () => ({
			filters: { customer: frm.doc.customer, company: frm.doc.company, is_return: 1 },
		}));
		frm.set_query("replacement_delivery_reference", () => ({
			filters: { customer: frm.doc.customer, company: frm.doc.company },
		}));
	},

	refresh(frm) {
		add_view_buttons(frm);
		if (frm.is_new()) return;

		if (frm.doc.status === "Draft") {
			frm.add_custom_button(
				__("Submit Request"),
				() => transition(frm, "request"),
				__("Actions")
			);
		}
		if (["Draft", "Requested"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Approve"), () => transition(frm, "approve"), __("Actions"));
		}
		if (["Approved", "Awaiting Return", "In Transit"].includes(frm.doc.status)) {
			frm.add_custom_button(
				__("Mark Received"),
				() => transition(frm, "receive"),
				__("Actions")
			);
		}
		if (["Received", "Under Inspection", "Awaiting Resolution"].includes(frm.doc.status)) {
			frm.add_custom_button(
				__("Start Inspection"),
				() => transition(frm, "inspect"),
				__("Actions")
			);
		}
		if (frm.doc.status === "Received" || frm.doc.status === "Under Inspection") {
			frm.add_custom_button(
				__("Prepare Return Stock Document"),
				() => prepare_return_stock_document(frm),
				__("Actions")
			);
		}
		if (["Awaiting Resolution", "Under Inspection"].includes(frm.doc.status)) {
			frm.add_custom_button(
				__("Mark Resolved"),
				() => transition(frm, "resolve"),
				__("Actions")
			);
		}
		if (frm.doc.status === "Resolved") {
			frm.add_custom_button(__("Close"), () => transition(frm, "close"), __("Actions"));
		}
	},
});

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
				frappe.show_alert({
					message: __("Return Delivery Note {0} prepared", [response.message]),
					indicator: "green",
				});
				frm.reload_doc();
			}
		},
	});
}

function add_view_buttons(frm) {
	[
		["delivery_note", "Delivery Note", __("Original Delivery Note")],
		["sales_order", "Sales Order", __("Sales Order")],
		["delivery_trip", "Delivery Trip", __("Delivery Trip")],
		["proof_of_delivery", "Proof of Delivery", __("Proof of Delivery")],
		["return_stock_document", "Delivery Note", __("Return Stock Document")],
		["credit_note", "Sales Invoice", __("Credit Note")],
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
