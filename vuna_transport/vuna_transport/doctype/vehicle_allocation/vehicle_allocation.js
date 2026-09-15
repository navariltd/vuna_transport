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
		frappe.db.get_value("Vehicle", vehicle_name, ["vuna_supplier"]).then(({ message }) => {
			if (!message || frm.doc.vehicle !== frm._vehicle_used_for_allocation_defaults) return;
			if (frm.doc.allocation_type === "Subcontractor") {
				frm.set_value("supplier", message.vuna_supplier || null);
			}
		});
	},

	allocation_type(frm) {
		frm.toggle_reqd("vehicle", ["Owned Fleet", "Subcontractor"].includes(frm.doc.allocation_type));
		frm.toggle_reqd("driver", frm.doc.allocation_type === "Owned Fleet");
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
	const dimension = (label, value, uom, detail) => {
		const utilization = detail?.utilization;
		const indicator = utilization > 100 ? "red" : utilization >= 85 ? "orange" : "green";
		const available = detail?.remaining == null ? "—" : frappe.format(detail.remaining, { fieldtype: "Float" });
		return `<div class="col-sm-4"><div class="text-muted small">${label}</div><div><b>${frappe.format(value || 0, { fieldtype: "Float" })} ${uom || ""}</b></div><div class="small text-${indicator}">${
			utilization == null ? __("Capacity not configured") : `${utilization}% · ${available} ${__("remaining")}`
		}</div></div>`;
	};
	$wrapper.html(
		`<div class="row small" style="padding: 4px 0">${dimension(
			__("Weight"),
			totals.weight,
			totals.weight_uom,
			capacity.weight
		)}${dimension(__("Volume"), totals.volume, totals.volume_uom, capacity.volume)}<div class="col-sm-4"><div class="text-muted small">${__(
			"Stops"
		)}</div><div><b>${totals.stops || 0}</b></div></div></div>`
	);
}
