frappe.query_reports["Loads Awaiting Vehicle"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{
			fieldname: "movement_type",
			label: __("Movement Type"),
			fieldtype: "Select",
			options: "\nCustomer Delivery\nWarehouse Transfer\nSupplier Pickup",
		},
		{
			fieldname: "source_address",
			label: __("Supplier Pickup Address"),
			fieldtype: "Link",
			options: "Address",
		},
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
