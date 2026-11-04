frappe.ui.form.on("Transport Demand", {
	setup(frm) {
		frm.set_query("source_warehouse", () => ({
			filters: { company: frm.doc.company, is_group: 0, disabled: 0 },
		}));
	},
	refresh(frm) {
		if (frm.doc.docstatus === 1 && frm.doc.movement_type === "Warehouse Transfer") {
			frm.add_custom_button(
				__("Transport Load"),
				async () => {
					const response = await frm.call("create_load");
					if (response.message)
						frappe.set_route("Form", "Transport Load", response.message);
				},
				__("Create")
			);
		}
	},
});
