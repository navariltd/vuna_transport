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
		add_get_delivered_items_button(frm);
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
		if (frm.doc.status === "Approved") {
			frm.add_custom_button(
				__("Await Return"),
				() => transition(frm, "await_return"),
				__("Actions")
			);
		}
		if (frm.doc.status === "Awaiting Return") {
			frm.add_custom_button(
				__("Mark In Transit"),
				() => transition(frm, "dispatch"),
				__("Actions")
			);
		}
		if (["Approved", "Awaiting Return", "In Transit"].includes(frm.doc.status)) {
			frm.add_custom_button(
				__("Mark Received"),
				() => transition(frm, "receive"),
				__("Actions")
			);
		}
		if (frm.doc.status === "Received") {
			frm.add_custom_button(
				__("Start Inspection"),
				() => transition(frm, "inspect"),
				__("Actions")
			);
		}
		if (frm.doc.status === "Under Inspection") {
			frm.add_custom_button(
				__("Await Resolution"),
				() => transition(frm, "await_resolution"),
				__("Actions")
			);
		}
		if (["Under Inspection", "Awaiting Resolution", "Resolved"].includes(frm.doc.status)) {
			frm.add_custom_button(
				__("Prepare Return Stock Document"),
				() => prepare_return_stock_document(frm),
				__("Actions")
			);
			if (
				(frm.doc.items || []).some(
					(row) => flt(row.received_qty) && !row.quality_inspection
				)
			) {
				frm.add_custom_button(
					__("Prepare Quality Inspections"),
					() => prepare_quality_inspections(frm),
					__("Actions")
				);
			}
		}
		if (frm.doc.status === "Awaiting Resolution") {
			frm.add_custom_button(
				__("Mark Resolved"),
				() => transition(frm, "resolve"),
				__("Actions")
			);
		}
		if (frm.doc.status === "Resolved") {
			frm.add_custom_button(__("Close"), () => transition(frm, "close"), __("Actions"));
		}
		if (!["Resolved", "Closed", "Cancelled"].includes(frm.doc.status)) {
			frm.add_custom_button(
				__("Record Custody Change"),
				() => record_custody_change(frm),
				__("Actions")
			);
		}
		add_resolution_actions(frm);
	},

	delivery_note(frm) {
		add_get_delivered_items_button(frm);
	},
});

frappe.ui.form.on("Return Request Item", {
	received_qty(frm, cdt, cdn) {
		const row = frappe.get_doc(cdt, cdn);
		frappe.model.set_value(
			cdt,
			cdn,
			"remaining_qty",
			Math.max(flt(row.requested_return_qty) - flt(row.received_qty), 0)
		);
	},
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
				frappe.show_alert({
					message: __("Return Delivery Note {0} prepared", [response.message]),
					indicator: "green",
				});
				frm.reload_doc();
			}
		},
	});
}

function prepare_quality_inspections(frm) {
	frappe.call({
		method: "vuna_transport.services.execution.return_service.create_return_quality_inspections",
		args: { return_request: frm.doc.name },
		freeze: true,
		callback: ({ message }) => {
			if (message?.length) {
				frappe.show_alert({
					message: __("{0} Quality Inspection document(s) prepared", [message.length]),
					indicator: "green",
				});
				frm.reload_doc();
			}
		},
	});
}

function record_custody_change(frm) {
	frappe.prompt(
		[
			{
				fieldname: "to_custody",
				fieldtype: "Select",
				label: __("Custody Now With"),
				options:
					"Still With Vehicle\nLeft With Customer\nHeld By Transporter\nReturned To Depot\nTransferred To Another Vehicle",
				reqd: 1,
			},
			{
				fieldname: "responsible_party",
				fieldtype: "Select",
				label: __("Responsible Party"),
				options: "Company\nCustomer\nTransporter\nDriver\nThird Party",
				reqd: 1,
			},
			{ fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks") },
		],
		(values) => {
			frappe.call({
				method: "vuna_transport.services.execution.return_service.record_custody_transfer",
				args: { return_request: frm.doc.name, ...values },
				freeze: true,
				callback: () => frm.reload_doc(),
			});
		},
		__("Record Custody Change"),
		__("Record")
	);
}

function add_resolution_actions(frm) {
	if (frm.doc.commercial_resolution === "Credit Note" && !frm.doc.credit_note) {
		frm.add_custom_button(
			__("Find Credit Note"),
			() =>
				frappe.set_route("List", "Sales Invoice", {
					customer: frm.doc.customer,
					is_return: 1,
				}),
			__("Resolve")
		);
	}
	if (
		["Replacement", "Re-delivery"].includes(frm.doc.commercial_resolution) &&
		!frm.doc.replacement_delivery_reference
	) {
		frm.add_custom_button(
			__("Link Follow-up Delivery"),
			() => frm.scroll_to_field("replacement_delivery_reference"),
			__("Resolve")
		);
	}
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
