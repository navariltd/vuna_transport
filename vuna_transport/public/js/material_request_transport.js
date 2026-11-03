frappe.ui.form.on("Material Request", {
	async refresh(frm) {
		if (frm.doc.docstatus !== 1 || frm.doc.material_request_type !== "Material Transfer")
			return;
		const policy = await frappe.call({
			method: "vuna_transport.services.demand.source_policy_service.get_transport_source_policy",
			args: { source_type: "Material Request" },
		});
		if (!policy.message?.enabled || frm.doc.docstatus !== 1) return;
		frm.add_custom_button(
			__("Transport Demand"),
			async () => {
				const response = await frappe.call({
					method: "vuna_transport.services.demand.transport_demand_service.create_transport_demands",
					args: { source_doctype: "Material Request", source_name: frm.doc.name },
					freeze: true,
				});
				const names = response.message || [];
				if (names.length === 1) frappe.set_route("Form", "Transport Demand", names[0]);
				else
					frappe.set_route("List", "Transport Demand", {
						source_document: frm.doc.name,
					});
			},
			__("Create")
		);
	},
});
