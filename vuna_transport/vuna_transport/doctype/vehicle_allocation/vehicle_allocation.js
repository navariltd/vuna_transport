// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vehicle Allocation", {
	setup(frm) {
		frm.set_query("vehicle", () => {
			if (!frm.doc.delivery_load) return { filters: { name: "" } };
			return {
				query: "vuna_transport.api.get_vehicles_for_load",
				filters: {
					delivery_load: frm.doc.delivery_load,
					allocation_type: frm.doc.allocation_type,
				},
			};
		});
		frm.set_query("driver", () => {
			if (!frm.doc.delivery_load) return { filters: { status: "Active" } };
			return {
				query: "vuna_transport.api.get_drivers_for_load",
				filters: { delivery_load: frm.doc.delivery_load },
			};
		});
	},

	refresh(frm) {
		if (frm.doc.delivery_load) {
			frm.add_custom_button(
				__("Open Delivery Load"),
				() => {
					frappe.set_route("Form", "Delivery Load", frm.doc.delivery_load);
				},
				__("View")
			);
			frm.add_custom_button(
				__("Allocation History"),
				() => {
					frappe.set_route("List", "Vehicle Allocation", {
						delivery_load: frm.doc.delivery_load,
					});
				},
				__("View")
			);
		}
	},

	vehicle(frm) {
		const vehicle_name = frm.doc.vehicle;
		if (!vehicle_name) {
			frm.set_value("subcontractor_vehicle_category", null);
			return;
		}
		frm._vehicle_used_for_allocation_defaults = vehicle_name;
		frappe.db
			.get_value("Vehicle", vehicle_name, ["vuna_supplier", "vuna_vehicle_category"])
			.then(({ message }) => {
				if (!message || frm.doc.vehicle !== frm._vehicle_used_for_allocation_defaults)
					return;
				frm.set_value(
					"subcontractor_vehicle_category",
					message.vuna_vehicle_category || null
				);
				if (frm.doc.allocation_type === "Subcontractor") {
					frm.set_value("supplier", message.vuna_supplier || null);
				}
			});
	},

	delivery_load(frm) {
		if (!frm.doc.delivery_load) return;
		frappe.db
			.get_value("Delivery Load", frm.doc.delivery_load, [
				"company",
				"required_vehicle_category",
				"planned_departure",
			])
			.then(({ message }) => {
				if (!message) return;
				frm.set_value("company", message.company);
				frm.set_value("required_vehicle_category", message.required_vehicle_category);
				if (!frm.doc.planned_reporting_time && message.planned_departure) {
					frm.set_value("planned_reporting_time", message.planned_departure);
				}
			});
	},

	allocation_type(frm) {
		frm.toggle_reqd(
			"vehicle",
			["Owned Fleet", "Subcontractor"].includes(frm.doc.allocation_type)
		);
		frm.toggle_reqd("driver", frm.doc.allocation_type === "Owned Fleet");
	},
});
