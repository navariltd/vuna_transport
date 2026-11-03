frappe.listview_settings["Transport Load"] = {
	add_fields: ["status", "dispatch_date", "source_warehouse", "pick_list"],
	get_indicator(doc) {
		const colors = {
			Draft: [__("Draft"), "gray"],
			Proposed: [__("Proposed"), "blue"],
			"Under Review": [__("Under Review"), "orange"],
			Approved: [__("Approved"), "green"],
			"Awaiting Vehicle": [__("Awaiting Vehicle"), "orange"],
			Allocated: [__("Allocated"), "green"],
			"Released to Warehouse": [__("Released to Warehouse"), "purple"],
			Loading: [__("Loading"), "yellow"],
			Dispatched: [__("Dispatched"), "blue"],
			Completed: [__("Completed"), "green"],
			"Completed with Exceptions": [__("Completed with Exceptions"), "orange"],
			Cancelled: [__("Cancelled"), "red"],
		};
		return colors[doc.status] || [__(doc.status), "gray"];
	},
};
