frappe.listview_settings["Transport Demand"] = {
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
