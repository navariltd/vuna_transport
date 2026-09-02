frappe.query_reports["Open Delivery Exceptions"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{
			fieldname: "delivery_load",
			label: __("Delivery Load"),
			fieldtype: "Link",
			options: "Delivery Load",
		},
		{
			fieldname: "exception_type",
			label: __("Exception Type"),
			fieldtype: "Select",
			options: "\nPartial Delivery\nRejected Delivery\nFailed Delivery",
		},
	],
};
