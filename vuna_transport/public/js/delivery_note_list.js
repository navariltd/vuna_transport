// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

(() => {
	const settings = frappe.listview_settings["Delivery Note"];
	if (!settings || settings._vuna_allocation_aware_trip_action) return;

	settings._vuna_allocation_aware_trip_action = true;
	settings.add_fields = [...new Set([...(settings.add_fields || []), "vuna_delivery_load"])];
	const standard_onload = settings.onload;

	settings.onload = function (listview) {
		standard_onload?.call(this, listview);
		configure_allocation_aware_delivery_trip_action(listview);
	};
})();

function configure_allocation_aware_delivery_trip_action(listview) {
	const original_on_row_checked = listview.on_row_checked.bind(listview);
	let selection_version = 0;

	listview.on_row_checked = function () {
		original_on_row_checked();
		update_delivery_trip_action_visibility();
	};

	function get_delivery_trip_action() {
		const encoded_label = encodeURIComponent(__("Create Delivery Trip"));
		return listview.page.actions
			.find(".menu-item-label")
			.filter((_, element) => $(element).attr("data-label") === encoded_label)
			.closest("li");
	}

	function update_delivery_trip_action_visibility() {
		const selected = listview.get_checked_items();
		const $action = get_delivery_trip_action();
		if (!$action.length) return;

		const delivery_loads = [
			...new Set(selected.map((row) => row.vuna_delivery_load).filter(Boolean)),
		];
		if (!selected.length || delivery_loads.length !== selected.length) {
			$action.show();
			return;
		}

		const current_selection = ++selection_version;
		frappe.db
			.get_list("Delivery Load", {
				filters: {
					name: ["in", delivery_loads],
					active_vehicle_allocation: ["is", "set"],
				},
				fields: ["name"],
				limit: delivery_loads.length,
			})
			.then((allocated_loads) => {
				if (current_selection !== selection_version) return;
				$action.toggle(allocated_loads.length !== delivery_loads.length);
			})
			.catch(() => $action.show());
	}
}
