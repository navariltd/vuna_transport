// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vehicle Onboarding Template", {
	setup(frm) {
		frm.set_query("transport_cost_structure", () => ({
			filters: { docstatus: 1, disabled: 0 },
		}));
		const document_type = frm.fields_dict.documents?.grid?.get_field("vehicle_document_type");
		if (document_type) document_type.get_query = () => ({ filters: { disabled: 0 } });
	},
});
