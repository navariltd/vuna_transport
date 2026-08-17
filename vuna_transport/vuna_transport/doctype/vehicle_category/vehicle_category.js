// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vehicle Category", {
	onload(frm) {
		if (!frm.is_new()) return;
		Promise.all([
			frappe.db.get_single_value("Transport Settings", "default_weight_uom"),
			frappe.db.get_single_value("Transport Settings", "default_volume_uom"),
		]).then(([weight_uom, volume_uom]) => {
			if (!frm.doc.max_payload_weight_uom && weight_uom) {
				frm.set_value("max_payload_weight_uom", weight_uom);
			}
			if (!frm.doc.max_volume_uom && volume_uom) {
				frm.set_value("max_volume_uom", volume_uom);
			}
		});
	},
});
