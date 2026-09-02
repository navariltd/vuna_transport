// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Vehicle Allocation History"] = {filters: [
	{fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company"},
	{fieldname: "vehicle", label: __("Vehicle"), fieldtype: "Link", options: "Vehicle"},
	{fieldname: "from_date", label: __("From Date"), fieldtype: "Date"},
	{fieldname: "to_date", label: __("To Date"), fieldtype: "Date"},
]};
