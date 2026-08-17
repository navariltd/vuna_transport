// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vehicle Allocation", {
	setup(frm) {
		frm.set_query("vehicle", () => {
			if (!frm.doc.delivery_load) return { filters: { name: "" } };
			return {
				query: "vuna_transport.api.get_vehicles_for_load",
				filters: { delivery_load: frm.doc.delivery_load },
			};
		});
		frm.set_query("driver", () => ({ filters: { status: "Active" } }));
	},

	refresh(frm) {
		if (frm.doc.delivery_load) {
			frm.add_custom_button(__("Open Delivery Load"), () => {
				frappe.set_route("Form", "Delivery Load", frm.doc.delivery_load);
			}, __("View"));
			frm.add_custom_button(__("Allocation History"), () => {
				frappe.set_route("List", "Vehicle Allocation", {
					delivery_load: frm.doc.delivery_load,
				});
			}, __("View"));
		}
	},

	delivery_load(frm) {
		if (!frm.doc.delivery_load) return;
		frappe.db.get_value(
			"Delivery Load",
			frm.doc.delivery_load,
			["company", "required_vehicle_category", "planned_departure"],
		).then(({ message }) => {
			if (!message) return;
			frm.set_value("company", message.company);
			frm.set_value("required_vehicle_category", message.required_vehicle_category);
			if (!frm.doc.planned_reporting_time && message.planned_departure) {
				frm.set_value("planned_reporting_time", message.planned_departure);
			}
		});
	},
});
