frappe.listview_settings["Transport Demand"] = {
	add_fields: ["status", "movement_type"],
	has_indicator_for_draft: true,
	get_indicator(doc) {
		const status =
			doc.docstatus === 2 ? "Cancelled" : doc.docstatus === 0 ? "Draft" : doc.status;
		const colors = {
			Draft: "gray",
			Open: "blue",
			"Partially Allocated": "orange",
			Reserved: "orange",
			Allocated: "green",
			Dispatched: "blue",
			"Partially Delivered": "orange",
			Delivered: "green",
			Closed: "gray",
			Cancelled: "red",
		};
		return [__(status), colors[status] || "gray", `status,=,${status}`];
	},
	onload(listview) {
		listview.page.add_action_item(__("Create Transport Load"), async () => {
			const names = listview.get_checked_items().map((row) => row.name);
			if (!names.length) return;
			const response = await frappe.call({
				method: "vuna_transport.services.planning.transport_load_service.bundle_transport_demands",
				args: { demand_names: names },
				freeze: true,
			});
			if (response.message) frappe.set_route("Form", "Transport Load", response.message);
		});
	},
};
