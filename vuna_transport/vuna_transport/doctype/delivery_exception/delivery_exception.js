// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Delivery Exception", {
	setup(frm) {
		frm.set_query("return_document", () => ({
			filters: {
				is_return: 1,
				return_against: frm.doc.delivery_note,
			},
		}));
		frm.set_query("credit_note", () => ({
			filters: {
				customer: frm.doc.customer,
				is_return: 1,
			},
		}));
		frm.set_query("replacement_delivery_reference", () => ({
			filters: {
				customer: frm.doc.customer,
				company: frm.doc.company,
				name: ["!=", frm.doc.delivery_order],
			},
		}));
	},

	refresh(frm) {
		add_view_buttons(frm);
		if (frm.is_new()) return;
		if (frm.doc.docstatus === 0 && (frm.doc.items || []).length) {
			frm.add_custom_button(
				__("Prepare Resolution Plan"),
				() => prepare_resolution_plan(frm),
				__("Resolve")
			);
		}
		if (
			frm.doc.docstatus === 1 &&
			has_commercial_resolution(frm.doc, "Replacement") ||
			frm.doc.docstatus === 1 &&
			has_commercial_resolution(frm.doc, "Re-delivery")
		) {
			if (frm.doc.replacement_sales_order) {
				frm.add_custom_button(
					__("Replacement Sales Order"),
					() => frappe.set_route("Form", "Sales Order", frm.doc.replacement_sales_order),
					__("View")
				);
			} else {
				frm.add_custom_button(
					__("Create Replacement Sales Order"),
					() => create_replacement_sales_order(frm),
					__("Resolve")
				);
			}
		}
		if (requires_stock_return(frm.doc)) {
			if (frm.doc.return_request) {
				frm.add_custom_button(
					__("Return Request"),
					() => frappe.set_route("Form", "Return Request", frm.doc.return_request),
					__("View")
				);
			} else {
				frm.add_custom_button(
					__("Create Return Request"),
					() => create_return_request(frm),
					__("Resolve")
				);
			}
		}

		if (requires_stock_return(frm.doc) && !frm.doc.return_document && !frm.doc.return_request) {
			frm.add_custom_button(
				__("Prepare Stock Return"),
				() => {
					frappe.show_alert(
						{
							message: __(
								"Use Create > Sales Return on the original Delivery Note, then link the submitted return here."
							),
							indicator: "blue",
						},
						8
					);
					frappe.set_route("Form", "Delivery Note", frm.doc.delivery_note);
				},
				__("Resolve")
			);
		}

		if (has_commercial_resolution(frm.doc, "Credit Note") && !frm.doc.credit_note) {
			frm.add_custom_button(
				__("Find Credit Note"),
				() => {
					frappe.set_route("List", "Sales Invoice", {
						customer: frm.doc.customer,
						is_return: 1,
					});
				},
				__("Resolve")
			);
		}

		if (
			["Replacement", "Re-delivery"].some((resolution) =>
				has_commercial_resolution(frm.doc, resolution)
			)
		) {
			frm.add_custom_button(
				__("Link Follow-up Delivery"),
				() => {
					frm.scroll_to_field("replacement_delivery_reference");
					frappe.show_alert(
						{
							message: __(
								"Create the legitimate replacement or re-delivery demand through the normal Sales Order flow, then link its Delivery Order here."
							),
							indicator: "blue",
						},
						8
					);
				},
				__("Resolve")
			);
		}
	},
});

function add_view_buttons(frm) {
	[
		["proof_of_delivery", "Proof of Delivery", __("POD")],
		["delivery_trip", "Delivery Trip", __("Delivery Trip")],
		["delivery_note", "Delivery Note", __("Delivery Note")],
		["delivery_order", "Delivery Order", __("Delivery Order")],
		["return_document", "Delivery Note", __("Return Document")],
		["credit_note", "Sales Invoice", __("Credit Note")],
		["replacement_sales_order", "Sales Order", __("Replacement Sales Order")],
		["replacement_delivery_reference", "Delivery Order", __("Follow-up Delivery")],
		["return_request", "Return Request", __("Return Request")],
	].forEach(([fieldname, doctype, label]) => {
		if (!frm.doc[fieldname]) return;
		frm.add_custom_button(
			label,
			() => {
				frappe.set_route("Form", doctype, frm.doc[fieldname]);
			},
			__("View")
		);
	});
}

function create_return_request(frm) {
	frappe.call({
		method: "vuna_transport.services.execution.return_service.create_return_request_from_exception",
		args: { delivery_exception: frm.doc.name },
		freeze: true,
		callback: ({ message }) => {
			if (message) frappe.set_route("Form", "Return Request", message);
		},
	});
}

function create_replacement_sales_order(frm) {
	frappe.call({
		method: "vuna_transport.services.execution.return_service.create_replacement_sales_order_from_exception",
		args: { delivery_exception: frm.doc.name },
		freeze: true,
		callback: ({ message }) => {
			if (message) frappe.set_route("Form", "Sales Order", message);
		},
	});
}

function prepare_resolution_plan(frm) {
	frm.clear_table("resolutions");
	(frm.doc.items || []).forEach((item) => {
		if (!flt(item.unresolved_qty)) return;
		const row = frm.add_child("resolutions");
		row.delivery_exception_item = item.name;
		row.item_code = item.item_code;
		row.item_name = item.item_name;
		row.delivery_order_item = item.delivery_order_item;
		row.delivery_note_item = item.delivery_note_item;
		row.quantity = item.unresolved_qty;
		row.custody = item.custody || frm.doc.custody;
		row.responsible_party = item.responsible_party || frm.doc.responsible_party;
		row.physical_resolution = item.physical_resolution || frm.doc.physical_resolution;
		row.resolution_warehouse = frm.doc.resolution_warehouse;
		row.commercial_resolution = item.commercial_resolution || frm.doc.commercial_resolution;
	});
	frm.refresh_field("resolutions");
	frm.dirty();
}

function requires_stock_return(doc) {
	const resolutions = [
		"Return to Original Depot",
		"Return to Nearest Depot",
		"Customer Return to Depot",
	];
	return (
		resolutions.includes(doc.physical_resolution) ||
		(doc.items || []).some((row) => resolutions.includes(row.physical_resolution)) ||
		(doc.resolutions || []).some((row) => resolutions.includes(row.physical_resolution))
	);
}

function has_commercial_resolution(doc, resolution) {
	return (
		doc.commercial_resolution === resolution ||
		(doc.items || []).some((row) => row.commercial_resolution === resolution) ||
		(doc.resolutions || []).some((row) => row.commercial_resolution === resolution)
	);
}
