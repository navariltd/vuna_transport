// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Transport Cost Structure Assignment", {
	onload(frm) {
		frm.__last_cost_structure = frm.doc.cost_structure || null;
	},

	setup(frm) {
		frm.set_query("vehicle", () => ({ filters: { company: frm.doc.company } }));
		frm.set_query("vehicle_category", () => ({ filters: { disabled: 0 } }));
		frm.set_query("source_warehouse", "destination_rates", () => ({
			filters: { company: frm.doc.company, is_group: 0, disabled: 0 },
		}));
		frm.set_query("county", "destination_rates", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return { filters: row.province ? { province: row.province } : {} };
		});
		frm.set_query("town", "destination_rates", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return { filters: row.county ? { county: row.county } : {} };
		});
	},

	cost_structure(frm) {
		if (!frm.doc.cost_structure) {
			frm.__last_cost_structure = null;
			return;
		}

		const populate = () => {
			frappe.db.get_doc("Transport Cost Structure", frm.doc.cost_structure).then((structure) => {
				frm.clear_table("components");
				(structure.components || []).forEach((component) => {
					const row = frm.add_child("components");
					row.cost_component = component.cost_component;
					row.calculation_basis = component.calculation_basis;
					row.cost_nature = component.cost_nature;
				});
				frm.refresh_field("components");
				frm.__last_cost_structure = frm.doc.cost_structure;
			});
		};

		if ((frm.doc.components || []).length && frm.__last_cost_structure !== frm.doc.cost_structure) {
			frappe.confirm(
				__("Replace the current assignment components with those from the selected Cost Structure?"),
				populate,
				() => frm.set_value("cost_structure", frm.__last_cost_structure || "")
			);
			return;
		}

		populate();
	},
});
