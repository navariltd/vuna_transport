// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Expiring Vehicle Documents"] = {filters: [
	{fieldname: "days", label: __("Expiring Within Days"), fieldtype: "Int", default: 30, reqd: 1},
	{fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company"},
]};
