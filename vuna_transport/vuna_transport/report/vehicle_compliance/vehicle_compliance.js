// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Vehicle Compliance"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{
			fieldname: "compliance_status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\nCompliant\nBlocked",
		},
	],
};
