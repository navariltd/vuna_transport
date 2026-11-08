frappe.query_reports["Delivery Notes Pending Submission"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{
			fieldname: "delivery_load",
			label: __("Transport Load"),
			fieldtype: "Link",
			options: "Transport Load",
		},
	],
};
