// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Dispatch Readiness"] = {filters: [
	{fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company"},
	{fieldname: "status", label: __("Load Status"), fieldtype: "Select", options: "\nDraft\nPlanned\nApproved\nAwaiting Vehicle\nAllocated\nDispatched\nCompleted\nCancelled"},
	{fieldname: "from_date", label: __("Dispatch From"), fieldtype: "Date"},
	{fieldname: "to_date", label: __("Dispatch To"), fieldtype: "Date"},
]};
