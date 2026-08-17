// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vehicle", {
	vuna_vehicle_model(frm) {
		if (!frm.doc.vuna_vehicle_model) return;
		frappe.db.get_value(
			"Vehicle Model",
			frm.doc.vuna_vehicle_model,
			["manufacturer", "model_name", "vehicle_category", "fuel_type"],
		).then(({ message }) => {
			if (!message) return;
			frm.set_value("make", message.manufacturer);
			frm.set_value("model", message.model_name);
			frm.set_value("vuna_vehicle_category", message.vehicle_category);
			if (["Petrol", "Diesel", "Natural Gas", "Electric"].includes(message.fuel_type)) {
				frm.set_value("fuel_type", message.fuel_type);
			}
		});
	},
});
