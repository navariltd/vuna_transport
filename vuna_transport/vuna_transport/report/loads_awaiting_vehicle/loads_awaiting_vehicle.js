frappe.query_reports["Loads Awaiting Vehicle"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{
			fieldname: "source_warehouse",
			label: __("Source Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
		},
		{
			fieldname: "required_vehicle_category",
			label: __("Required Category"),
			fieldtype: "Link",
			options: "Vehicle Category",
		},
	],
};
