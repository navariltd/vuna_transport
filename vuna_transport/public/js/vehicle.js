// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vehicle", {
	refresh(frm) {
		render_operational_status(frm);
	},

	vuna_vehicle_model(frm) {
		if (!frm.doc.vuna_vehicle_model) return;
		frappe.db.get_value(
			"Vehicle Model",
			frm.doc.vuna_vehicle_model,
			["manufacturer", "model_name", "vehicle_category", "fuel_type"],
		).then(({ message }) => {
			if (!message) return;
			frm.set_value("make", message.manufacturer);
			frm.set_value("model", message.model_name);
			frm.set_value("vuna_vehicle_category", message.vehicle_category);
			if (["Petrol", "Diesel", "Natural Gas", "Electric"].includes(message.fuel_type)) {
				frm.set_value("fuel_type", message.fuel_type);
			}
		});
	},
});

function render_operational_status(frm) {
	const field = frm.fields_dict.vuna_operational_status_html;
	if (!field) return;
	if (frm.is_new()) {
		field.$wrapper.html(
			`<div class="text-muted">${__("Save the Vehicle to view its operational status.")}</div>`,
		);
		return;
	}

	field.$wrapper.html(`<div class="text-muted">${__("Loading operational status...")}</div>`);
	frappe.call({
		method: "vuna_transport.services.fleet_service.get_vehicle_operational_status",
		args: { vehicle: frm.doc.name },
	}).then(({ message }) => {
		if (!message || frm.doc.name !== message.vehicle) return;
		field.$wrapper.html(get_operational_status_html(message));
		field.$wrapper.find(".vuna-refresh-vehicle-status").on("click", () => {
			render_operational_status(frm);
		});
	}).catch(() => {
		field.$wrapper.html(
			`<div class="text-muted">${__("Operational status could not be loaded.")}</div>`,
		);
	});
}

function get_operational_status_html(status) {
	const escape = (value) => frappe.utils.escape_html(String(value ?? ""));
	const indicator = status.dispatch_eligible ? "green" : "orange";
	const reference = (doctype, name) => name
		? `<a href="/app/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}">${escape(name)}</a>`
		: `<span class="text-muted">${__("None")}</span>`;
	const next_available = status.next_available_at
		? frappe.datetime.str_to_user(status.next_available_at)
		: status.dispatch_eligible ? __("Now") : __("Not determined");

	return `<div class="vuna-vehicle-operational-status" style="border:1px solid var(--border-color);border-radius:var(--border-radius-md);padding:var(--padding-md);background:var(--control-bg);">
		<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;">
			<div>
				<span class="indicator-pill ${indicator}">${escape(status.operational_state)}</span>
				<span class="text-muted" style="margin-left:8px;">${status.dispatch_eligible ? __("Dispatch eligible now") : __("Not dispatch eligible now")}</span>
			</div>
			<button type="button" class="btn btn-xs btn-default vuna-refresh-vehicle-status">${__("Refresh Status")}</button>
		</div>
		<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-top:14px;">
			<div><div class="text-muted small">${__("Reason")}</div><div>${escape(status.primary_reason || __("No operational blockers"))}</div></div>
			<div><div class="text-muted small">${__("Current Allocation")}</div><div>${reference("Vehicle Allocation", status.current_allocation)}</div></div>
			<div><div class="text-muted small">${__("Current Trip")}</div><div>${reference("Delivery Trip", status.current_trip)}</div></div>
			<div><div class="text-muted small">${__("Next Available")}</div><div>${escape(next_available)}</div></div>
		</div>
	</div>`;
}
