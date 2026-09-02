// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vehicle Incident", {
	setup(frm) {
		frm.set_query("vehicle", () => ({
			filters: frm.doc.company ? { company: frm.doc.company } : {},
		}));
		frm.set_query("delivery_trip", () => ({
			filters: frm.doc.delivery_load ? { vuna_delivery_load: frm.doc.delivery_load } : {},
		}));
		frm.set_query("vehicle_allocation", () => ({
			filters: frm.doc.delivery_load ? { delivery_load: frm.doc.delivery_load } : {},
		}));
	},

	refresh(frm) {
		add_view_buttons(frm);
		if (frm.is_new() || ["Resolved", "Closed"].includes(frm.doc.resolution_status)) return;

		if (
			frm.doc.replacement_required &&
			frm.doc.delivery_load &&
			frm.doc.vehicle_allocation &&
			!frm.doc.replacement_vehicle_allocation
		) {
			frm.add_custom_button(
				__("Reallocate Vehicle"),
				() => {
					frappe.model.open_mapped_doc({
						method: "vuna_transport.services.fleet.vehicle_incident_service.make_replacement_allocation_from_incident",
						frm,
						freeze_message: __("Preparing replacement allocation..."),
					});
				},
				__("Create")
			);
		}

		if (!frm.doc.asset_repair) {
			frm.add_custom_button(
				__("Asset Repair"),
				() => {
					frappe
						.call({
							method: "vuna_transport.services.fleet.vehicle_incident_service.create_asset_repair",
							args: { source_name: frm.doc.name },
							freeze: true,
							freeze_message: __("Creating Asset Repair..."),
						})
						.then((response) => {
							if (response.message)
								frappe.set_route("Form", "Asset Repair", response.message);
						});
				},
				__("Create")
			);
		}
	},
});

function add_view_buttons(frm) {
	[
		["vehicle", "Vehicle", __("Vehicle")],
		["delivery_load", "Delivery Load", __("Delivery Load")],
		["delivery_trip", "Delivery Trip", __("Delivery Trip")],
		["vehicle_allocation", "Vehicle Allocation", __("Affected Allocation")],
		["replacement_vehicle_allocation", "Vehicle Allocation", __("Replacement Allocation")],
		["asset_repair", "Asset Repair", __("Asset Repair")],
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
