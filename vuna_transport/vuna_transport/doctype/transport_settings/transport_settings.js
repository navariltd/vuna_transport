// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Transport Settings", {
	setup(frm) {
		frm.set_query("source_document_type", "demand_sources", () => ({
			query: "vuna_transport.services.demand.source_policy_service.get_supported_source_query",
		}));
	},
});
