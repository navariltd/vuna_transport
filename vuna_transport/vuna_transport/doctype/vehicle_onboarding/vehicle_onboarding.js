frappe.ui.form.on("Vehicle Onboarding", {
	setup(frm) {
		frm.set_query("transport_cost_structure", () => ({
			filters: {
				company: ["in", [frm.doc.company, "", null]],
				vehicle_owner: frm.doc.vehicle_owner,
				disabled: 0,
				docstatus: 1,
			},
		}));
	},

	vehicle_category(frm) {
		if (!frm.doc.vehicle_category) return;

		frappe.db
			.get_value("Vehicle Category", frm.doc.vehicle_category, "vehicle_model")
			.then(({ message }) => {
				if (message?.vehicle_model) {
					frm.set_value("vehicle_model", message.vehicle_model);
				}
			});
	},

	vehicle_owner(frm) {
		if (frm.doc.transport_cost_structure) {
			frm.set_value("transport_cost_structure", null);
		}
		if (frm.doc.vehicle_owner === "Company" && frm.doc.supplier) {
			frm.set_value("supplier", null);
		}
	},
});
