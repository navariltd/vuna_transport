frappe.query_reports["Delivery Trips Pending POD"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{
			fieldname: "delivery_load",
			label: __("Delivery Load"),
			fieldtype: "Link",
			options: "Delivery Load",
		},
	],
};
