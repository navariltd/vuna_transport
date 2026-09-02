frappe.listview_settings["Delivery Exception"] = {
	add_fields: ["resolution_status", "exception_type", "customer", "follow_up_date"],
	get_indicator(doc) {
		const colors = { Open: "red", "In Resolution": "orange", Resolved: "green", Closed: "gray" };
		return [__(doc.resolution_status), colors[doc.resolution_status] || "gray"];
	},
};
