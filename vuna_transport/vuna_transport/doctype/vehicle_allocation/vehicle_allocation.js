// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vehicle Allocation", {
	setup(frm) {
		frm.set_query("cost_structure", () => ({
			filters: {
				docstatus: 1,
				disabled: 0,
				...(frm.doc.company ? { company: ["in", [frm.doc.company, "", null]] } : {}),
			},
		}));
		frm.set_query("delivery_load", "delivery_loads", () => ({
			query: "vuna_transport.api.get_delivery_loads_for_allocation",
			filters: {
				company: frm.doc.company,
				vehicle: frm.doc.vehicle,
				required_vehicle_category: frm.doc.required_vehicle_category,
				selected_delivery_loads: (frm.doc.delivery_loads || [])
					.map((row) => row.delivery_load)
					.filter(Boolean),
			},
		}));
		frm.set_query("vehicle", () => ({
			query: "vuna_transport.api.get_vehicles_for_allocation",
			filters: {
				company: frm.doc.company,
				required_vehicle_category: frm.doc.required_vehicle_category,
			},
		}));
		frm.set_query("driver", () => ({
			query: "vuna_transport.api.get_drivers_for_allocation",
			filters: {
				company: frm.doc.company,
				delivery_loads: (frm.doc.delivery_loads || []).map((row) => row.delivery_load).filter(Boolean),
			},
		}));
	},

	refresh(frm) {
		update_load_selection_button(frm);
		frm.remove_custom_button(__("Create Delivery Notes"), __("Execution"));
		frm.remove_custom_button(__("Create Delivery Trip"), __("Execution"));
		const loads = (frm.doc.delivery_loads || []).map((row) => row.delivery_load).filter(Boolean);
		if (frm.doc.docstatus === 1 && loads.length) {
			frm.add_custom_button(
				__("Create Delivery Notes"),
				() => frm.call("create_delivery_notes").then(({ message }) => {
					if (!message) return;
					if (message.length === 1) {
						frappe.set_route("Form", "Delivery Note", message[0]);
					} else {
						frappe.set_route("List", "Delivery Note", { name: ["in", message] });
					}
				}),
				__("Execution")
			);
			frm.add_custom_button(
				__("Create Delivery Trip"),
				() => frappe.model.open_mapped_doc({
					method: "vuna_transport.services.fleet.vehicle_allocation_service.make_delivery_trip_from_allocation",
					frm,
					freeze_message: __("Preparing Delivery Trip..."),
				}),
				__("Execution")
			);
		}
		if (loads.length) {
			frappe.call({
				method: "vuna_transport.services.fleet.vehicle_allocation_service.get_allocation_load_context",
				args: { delivery_loads: loads },
			}).then(({ message }) => render_capacity_summary(frm, message));
			frm.add_custom_button(
				__("Open Delivery Loads"),
				() => frappe.set_route("List", "Delivery Load", { name: ["in", loads] }),
				__("View")
			);
		}
	},

	vehicle(frm) {
		const vehicle_name = frm.doc.vehicle;
		if (!vehicle_name) return;
		frm._vehicle_used_for_allocation_defaults = vehicle_name;
		frappe.db
			.get_value("Vehicle", vehicle_name, ["vuna_supplier", "vuna_vehicle_owner", "vuna_vehicle_category", "vuna_transport_cost_structure"])
			.then(({ message }) => {
			if (!message || frm.doc.vehicle !== frm._vehicle_used_for_allocation_defaults) return;
			if (
				frm.doc.required_vehicle_category &&
				message.vuna_vehicle_category !== frm.doc.required_vehicle_category
			) {
				frm.set_value("vehicle", null);
				frappe.msgprint(
					__("Vehicle {0} belongs to category {1}, but this allocation requires {2}.", [
						vehicle_name,
						message.vuna_vehicle_category || __("Not Set"),
						frm.doc.required_vehicle_category,
					])
				);
				return;
			}
			frm.set_value("vehicle_owner", message.vuna_vehicle_owner || null);
			frm.set_value("allocation_type", message.vuna_vehicle_owner === "Supplier" ? "Subcontractor" : "Owned Fleet");
			frm.set_value("supplier", message.vuna_supplier || null);
			if (!frm.doc.cost_structure && message.vuna_transport_cost_structure)
				frm.set_value("cost_structure", message.vuna_transport_cost_structure);
		});
	},

	required_vehicle_category(frm) {
		update_load_selection_button(frm);
		if (frm.doc.vehicle) frm.trigger("vehicle");
	},

	allocation_type(frm) {
		frm.toggle_reqd("vehicle", ["Owned Fleet", "Subcontractor"].includes(frm.doc.allocation_type));
		frm.toggle_reqd("driver", true);
	},

	cost_structure(frm) {
		if (!frm.doc.vehicle || !frm.doc.cost_structure) return;
		frappe.db.get_value("Vehicle", frm.doc.vehicle, "vuna_transport_cost_structure").then(({ message }) => {
			if (message?.vuna_transport_cost_structure === frm.doc.cost_structure) return;
			frm.set_value("cost_structure", message?.vuna_transport_cost_structure || null);
			frappe.msgprint(
				__("The selected cost structure must match the Vehicle's configured Transport Cost Structure.")
			);
		});
	},
});

function select_delivery_loads(frm) {
	if (!frm.doc.company) {
		frappe.msgprint(__("Select a Company before selecting Delivery Loads."));
		return;
	}
	if (!frm.doc.required_vehicle_category) {
		frappe.msgprint(__("Select a Required Vehicle Category first."));
		return;
	}

	new frappe.ui.form.MultiSelectDialog({
		doctype: "Delivery Load",
		target: frm,
		size: "extra-large",
		add_filters_group: 1,
		columns: ["name", "company", "required_vehicle_category"],
		setters: {
			company: frm.doc.company,
			required_vehicle_category: frm.doc.required_vehicle_category,
		},
		get_query() {
			return {
				query: "vuna_transport.api.get_delivery_loads_for_allocation",
				filters: {
					company: frm.doc.company,
					required_vehicle_category: frm.doc.required_vehicle_category,
					selected_delivery_loads: (frm.doc.delivery_loads || [])
						.map((row) => row.delivery_load)
						.filter(Boolean),
				},
			};
		},
		action(selections) {
			if (!selections.length) {
				frappe.msgprint(__("Select at least one Delivery Load."));
				return;
			}
			const existing = new Set(
				(frm.doc.delivery_loads || []).map((row) => row.delivery_load).filter(Boolean)
			);
			selections.forEach((name) => {
				if (!existing.has(name)) frm.add_child("delivery_loads", { delivery_load: name });
			});
			frm.refresh_field("delivery_loads");
			refresh_load_context(frm);
			cur_dialog.hide();
		},
	});
}

function update_load_selection_button(frm) {
	frm.remove_custom_button(__("Select Delivery Loads"), __("Planning"));
	if (frm.doc.docstatus === 0 && frm.doc.required_vehicle_category) {
		frm.add_custom_button(
			__("Select Delivery Loads"),
			() => select_delivery_loads(frm),
			__("Planning")
		);
	}
}

function refresh_load_context(frm) {
	const loads = (frm.doc.delivery_loads || []).map((row) => row.delivery_load).filter(Boolean);
	if (!loads.length) return;
	frappe.call({
		method: "vuna_transport.services.fleet.vehicle_allocation_service.get_allocation_load_context",
		args: { delivery_loads: loads },
	}).then(({ message }) => {
		if (!message) return;
		frm.set_value("company", message.company || null);
		if (!frm.doc.required_vehicle_category) {
			frm.set_value("required_vehicle_category", message.required_vehicle_category || null);
		}
		if (!frm.doc.planned_reporting_time && message.planned_reporting_time)
			frm.set_value("planned_reporting_time", message.planned_reporting_time);
		render_capacity_summary(frm, message);
	});
}

frappe.ui.form.on("Vehicle Allocation Load", {
	delivery_load(frm, cdt, cdn) {
		if (!frm.doc.required_vehicle_category) {
			frm.fields_dict.delivery_loads.grid.grid_rows_by_docname[cdn]?.remove();
			frappe.msgprint(__("Select a Required Vehicle Category before selecting Delivery Loads."));
			return;
		}
		frappe.call({
			method: "vuna_transport.services.fleet.vehicle_allocation_service.get_allocation_load_context",
			args: {
				delivery_loads: (frm.doc.delivery_loads || []).map((row) => row.delivery_load).filter(Boolean),
			},
		}).then(({ message }) => {
			if (!message) return;
			frm.set_value("company", message.company || null);
			frm.set_value("required_vehicle_category", message.required_vehicle_category || null);
			if (!frm.doc.planned_reporting_time && message.planned_reporting_time) {
				frm.set_value("planned_reporting_time", message.planned_reporting_time);
			}
			render_capacity_summary(frm, message);
			frm.refresh_field("delivery_loads");
		});
	},

	delivery_loads_remove(frm) {
		frappe.call({
			method: "vuna_transport.services.fleet.vehicle_allocation_service.get_allocation_load_context",
			args: {
				delivery_loads: (frm.doc.delivery_loads || []).map((row) => row.delivery_load).filter(Boolean),
			},
		}).then(({ message }) => {
			frm.set_value("company", message?.company || null);
			frm.set_value("required_vehicle_category", message?.required_vehicle_category || null);
			render_capacity_summary(frm, message);
		});
	},
});

function render_capacity_summary(frm, context) {
	const $wrapper = frm.get_field("allocation_capacity_summary")?.$wrapper;
	if (!$wrapper) return;
	if (!context?.totals) {
		$wrapper.empty();
		return;
	}
	const totals = context.totals;
	const capacity = context.capacity?.capacity || {};
	const number = (value) => frappe.format(value || 0, { fieldtype: "Float" });
	const status_class = (utilization) => {
		if (utilization >= 100) return "red";
		if (utilization >= 70) return "green";
		return "blue";
	};
	const rows = [
		[__("Weight"), totals.weight, totals.weight_uom, capacity.weight],
		[__("Volume"), totals.volume, totals.volume_uom, capacity.volume],
	]
		.filter(([, , , detail]) => detail?.capacity)
		.map(([label, used, _uom, detail]) => {
			const indicator = status_class(detail.utilization);
			return `<tr><td class="text-muted">${label}</td><td>${number(used)}</td><td>${number(detail.capacity)}</td><td class="${indicator}">${number(detail.remaining)}</td><td><span class="indicator-pill ${indicator}">${number(detail.utilization)}%</span></td></tr>`;
		})
		.join("");
	$wrapper.html(
		`<div class="vuna-capacity-summary"><table class="table table-bordered table-condensed" style="margin:0"><thead><tr><th>${__(
			"Dimension"
		)}</th><th>${__("Occupied")}</th><th>${__("Capacity")}</th><th>${__("Available")}</th><th style="width:110px">${__(
			"Used"
		)}</th></tr></thead><tbody>${rows}</tbody></table></div>`
	);
}
