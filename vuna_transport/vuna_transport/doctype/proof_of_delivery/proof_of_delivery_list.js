frappe.listview_settings["Proof of Delivery"] = {
	add_fields: ["status", "customer", "delivery_trip", "delivery_datetime"],
	get_indicator(doc) {
		const colors = { Pending: "orange", Accepted: "green", "Partially Delivered": "yellow", Rejected: "red", Failed: "red" };
		return [__(doc.status), colors[doc.status] || "gray"];
	},
};
