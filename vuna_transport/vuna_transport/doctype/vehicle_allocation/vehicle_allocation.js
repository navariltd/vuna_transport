// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vehicle Allocation", {
	setup(frm) {
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
				delivery_loads: (frm.doc.delivery_loads || []).map((row) => row.delivery_load).filter(Boolean),
				allocation_type: frm.doc.allocation_type,
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
		const loads = (frm.doc.delivery_loads || []).map((row) => row.delivery_load).filter(Boolean);
		if (frm.doc.docstatus === 1 && loads.length) {
			frappe.db.get_single_value("Transport Settings", "vehicle_allocation_timing").then((timing) => {
				frm.remove_custom_button(__("Create Delivery Notes"), __("Execution"));
				frm.remove_custom_button(__("Create Delivery Trip"), __("Execution"));
				const label = timing === "Before Delivery Notes" ? __("Create Delivery Notes") : __("Create Delivery Trip");
				const method = timing === "Before Delivery Notes" ? "create_delivery_notes" : "create_delivery_trip";
				frm.add_custom_button(
					label,
					() => {
						if (method === "create_delivery_trip") {
							return frappe.model.open_mapped_doc({
								method: "vuna_transport.services.fleet.vehicle_allocation_service.make_delivery_trip_from_allocation",
								frm,
								freeze_message: __("Preparing Delivery Trip..."),
							});
						}
						return frm.call(method).then(({ message }) => {
							if (!message) return;
							if (message.length === 1) {
								frappe.set_route("Form", "Delivery Note", message[0]);
							} else {
								frappe.set_route("List", "Delivery Note", { name: ["in", message] });
							}
						});
					},
					__("Execution")
				);
			});
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
		frappe.db.get_value("Vehicle", vehicle_name, ["vuna_supplier", "vuna_vehicle_owner"]).then(({ message }) => {
			if (!message || frm.doc.vehicle !== frm._vehicle_used_for_allocation_defaults) return;
			frm.set_value("vehicle_owner", message.vuna_vehicle_owner || null);
			if (frm.doc.allocation_type === "Subcontractor") {
				frm.set_value("supplier", message.vuna_supplier || null);
			}
		});
	},

	allocation_type(frm) {
		frm.toggle_reqd("vehicle", ["Owned Fleet", "Subcontractor"].includes(frm.doc.allocation_type));
		frm.toggle_reqd("driver", true);
	},
});

frappe.ui.form.on("Vehicle Allocation Load", {
	delivery_load(frm) {
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
