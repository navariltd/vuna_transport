// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.query_reports["Vehicle Incident Register"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{ fieldname: "vehicle", label: __("Vehicle"), fieldtype: "Link", options: "Vehicle" },
		{
			fieldname: "resolution_status",
			label: __("Resolution Status"),
			fieldtype: "Select",
			options: "\nOpen\nIn Resolution\nResolved\nClosed",
		},
		{ fieldname: "vehicle_immobilized", label: __("Vehicle Immobilized"), fieldtype: "Check" },
	],
};
