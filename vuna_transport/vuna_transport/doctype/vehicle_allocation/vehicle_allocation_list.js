frappe.listview_settings["Vehicle Allocation"] = {
	add_fields: ["status", "allocation_type", "vehicle", "driver"],
	get_indicator(doc) {
		const status = doc.status;
		const indicators = {
			Draft: [__("Draft"), "gray"],
			Allocated: [__("Allocated"), "green"],
			"Awaiting Delivery Notes": [__("Awaiting Delivery Notes"), "orange"],
			"Ready for Delivery Trip": [__("Ready for Delivery Trip"), "blue"],
			"Delivery Trip Draft": [__("Delivery Trip Draft"), "orange"],
			"Delivery Trip Scheduled": [__("Delivery Trip Scheduled"), "blue"],
			"In Transit": [__("In Transit"), "blue"],
			Completed: [__("Completed"), "green"],
			Accepted: [__("Accepted"), "green"],
			Rejected: [__("Rejected"), "red"],
			Replaced: [__("Replaced"), "gray"],
			Cancelled: [__("Cancelled"), "red"],
		};
		return indicators[status] || [__(status || "Draft"), "gray"];
	},
};
