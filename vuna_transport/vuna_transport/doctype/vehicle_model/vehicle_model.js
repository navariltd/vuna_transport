// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vehicle Model", {
	setup(frm) {
		const maximum_year = new Date().getFullYear() + 1;
		frm.set_df_property(
			"model_year",
			"description",
			__("Enter a year from 1886 to {0}, or leave blank if unknown.", [maximum_year])
		);
	},

	validate(frm) {
		if (!frm.doc.model_year) return;
		const year = cint(frm.doc.model_year);
		const maximum_year = new Date().getFullYear() + 1;
		if (year < 1886 || year > maximum_year) {
			frappe.throw(
				__("Model Year must be between 1886 and {0}, or left blank if unknown.", [
					maximum_year,
				])
			);
		}
	},

	onload(frm) {
		if (!frm.is_new()) return;
		Promise.all([
			frappe.db.get_single_value("Transport Settings", "default_weight_uom"),
			frappe.db.get_single_value("Transport Settings", "default_volume_uom"),
		]).then(([weight_uom, volume_uom]) => {
			if (!frm.doc.weight_uom && weight_uom) frm.set_value("weight_uom", weight_uom);
			if (!frm.doc.volume_uom && volume_uom) frm.set_value("volume_uom", volume_uom);
		});
	},
});
