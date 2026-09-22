frappe.ui.form.on("Vehicle Onboarding", {
	vehicle_category(frm) {
		if (!frm.doc.vehicle_category) return;

		frappe.db.get_value("Vehicle Category", frm.doc.vehicle_category, "vehicle_model").then(({ message }) => {
			if (message?.vehicle_model) {
				frm.set_value("vehicle_model", message.vehicle_model);
			}
		});
	},
});
