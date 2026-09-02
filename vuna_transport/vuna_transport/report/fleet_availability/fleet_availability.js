// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Fleet Availability"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{ fieldname: "from_datetime", label: __("From"), fieldtype: "Datetime" },
		{ fieldname: "to_datetime", label: __("To"), fieldtype: "Datetime" },
		{
			fieldname: "vehicle_category",
			label: __("Vehicle Category"),
			fieldtype: "Link",
			options: "Vehicle Category",
		},
	],
};
