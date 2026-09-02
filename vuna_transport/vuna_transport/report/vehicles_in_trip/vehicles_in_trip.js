// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Vehicles In Trip"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\nScheduled\nIn Transit\nCompleted\nCancelled",
		},
	],
};
