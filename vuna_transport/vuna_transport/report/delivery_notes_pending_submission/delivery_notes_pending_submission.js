frappe.query_reports["Delivery Notes Pending Submission"] = {
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
