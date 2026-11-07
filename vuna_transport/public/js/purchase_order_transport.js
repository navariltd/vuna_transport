frappe.ui.form.on("Purchase Order", {
	setup(frm) {
		frm.set_query("vuna_delivery_method", () => ({
			filters: {
				disabled: 0,
				buying: 1,
			},
		}));
		frm.set_query("vuna_pickup_address", () => ({
			query: "frappe.contacts.doctype.address.address.address_query",
			filters: { link_doctype: "Supplier", link_name: frm.doc.supplier || "", disabled: 0 },
		}));
	},
	async supplier(frm) {
		if (frm.doc.docstatus === 0) await frm.set_value("vuna_pickup_address", null);
	},
	async refresh(frm) {
		if (frm.doc.docstatus !== 1 || !frm.doc.vuna_delivery_method || frm.is_dirty()) return;
		if (["Closed", "Cancelled", "On Hold", "Stopped", "Completed"].includes(frm.doc.status))
			return;
		const name = frm.doc.name;
		const delivery_method = frm.doc.vuna_delivery_method;
		const { message: policy } = await frappe.call({
			method: "vuna_transport.services.demand.source_policy_service.get_transport_source_policy",
			args: { source_type: "Purchase Order", delivery_method: frm.doc.vuna_delivery_method },
		});
		if (
			!policy?.enabled ||
			!policy?.company_pickup ||
			frm.doc.vuna_delivery_method !== delivery_method ||
			frm.doc.name !== name ||
			frm.is_dirty() ||
			frm.doc.docstatus !== 1 ||
			!frm.doc.vuna_delivery_method
		)
			return;
		frm.add_custom_button(
			__("Transport Demand"),
			async () => {
				const { message: names } = await frappe.call({
					method: "vuna_transport.services.demand.transport_demand_service.create_transport_demands",
					args: { source_doctype: "Purchase Order", source_name: name },
					freeze: true,
				});
				if (names?.length === 1) frappe.set_route("Form", "Transport Demand", names[0]);
				else if (names?.length)
					frappe.set_route("List", "Transport Demand", {
						source_document_type: "Purchase Order",
						source_document: name,
					});
			},
			__("Create")
		);
		frm.add_custom_button(
			__("Purchase Receipt (Vehicle)"),
			() => {
				frappe.prompt(
					{
						fieldname: "allocation",
						fieldtype: "Link",
						options: "Vehicle Allocation",
						label: __("Pickup Vehicle Allocation"),
						reqd: 1,
						get_query: () => ({
							filters: {
								company: frm.doc.company,
								docstatus: 1,
								status: ["not in", ["Cancelled", "Replaced", "Completed"]],
								vehicle: ["is", "set"],
								driver: ["is", "set"],
							},
						}),
					},
					async ({ allocation }) => {
						const { message } = await frappe.call({
							method: "vuna_transport.services.execution.supplier_vehicle_receipt_service.create_vehicle_purchase_receipt",
							args: { source_name: name, allocation_name: allocation },
							freeze: true,
						});
						if (message) frappe.set_route("Form", "Purchase Receipt", message);
					},
					__("Receive Supplier Goods into Vehicle"),
					__("Create Draft")
				);
			},
			__("Create")
		);
	},
});
