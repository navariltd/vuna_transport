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

		if (requires_stock_return(frm.doc) && !frm.doc.return_document) {
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
		["replacement_delivery_reference", "Delivery Order", __("Follow-up Delivery")],
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

function requires_stock_return(doc) {
	const resolutions = [
		"Return to Original Depot",
		"Return to Nearest Depot",
		"Customer Return to Depot",
	];
	return (
		resolutions.includes(doc.physical_resolution) ||
		(doc.items || []).some((row) => resolutions.includes(row.physical_resolution))
	);
}

function has_commercial_resolution(doc, resolution) {
	return (
		doc.commercial_resolution === resolution ||
		(doc.items || []).some((row) => row.commercial_resolution === resolution)
	);
}
