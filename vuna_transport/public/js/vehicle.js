// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vehicle", {
	setup(frm) {
		frm.set_query("vuna_transport_cost_structure", () => ({
			filters: {
				company: ["in", [frm.doc.company, "", null]],
				vehicle_owner: frm.doc.vuna_vehicle_owner,
				disabled: 0,
				docstatus: 1,
			},
		}));
		frm.set_query("vuna_asset", () => ({
			query: "vuna_transport.services.fleet.fleet_service.get_unlinked_assets",
			filters: {
				company: frm.doc.company,
				current_vehicle: frm.doc.name,
			},
		}));
		const roles_field = frm.fields_dict.vuna_operational_roles;
		if (roles_field?.grid) {
			const role_field = roles_field.grid.get_field("operational_role");
			if (role_field) {
				role_field.get_query = () => ({
					filters: { disabled: 0 },
				});
			}
		}
	},

	refresh(frm) {
		if (!frm.is_new()) {
			const label = frm.doc.vuna_disabled ? __("Enable Vehicle") : __("Disable Vehicle");
			const button = frm.add_custom_button(
				label,
				() => {
					const disabled = Boolean(frm.doc.vuna_disabled);
					frappe.confirm(
						disabled
							? __("Enable Vehicle {0} for operations?", [frm.doc.name])
							: __("Disable Vehicle {0} from operations?", [frm.doc.name]),
						() => {
							frm.set_value("vuna_disabled", disabled ? 0 : 1);
							frm.save();
						}
					);
				},
				__("Operations")
			);
			if (!frm.doc.vuna_disabled) button.addClass("btn-danger");
		}
		if (!frm.is_new() && frm.doc.vuna_asset) {
			frm.add_custom_button(
				__("Linked Asset"),
				() => {
					frappe.set_route("Form", "Asset", frm.doc.vuna_asset);
				},
				__("View")
			);
		}
		render_operational_status(frm);
	},

	vuna_vehicle_model(frm) {
		if (!frm.doc.vuna_vehicle_model) return;
		frappe.db
			.get_value("Vehicle Model", frm.doc.vuna_vehicle_model, [
				"manufacturer",
				"model_name",
				"fuel_type",
			])
			.then(({ message }) => {
				if (!message) return;
				frm.set_value("make", message.manufacturer);
				frm.set_value("model", message.model_name);
				if (["Petrol", "Diesel", "Natural Gas", "Electric"].includes(message.fuel_type)) {
					frm.set_value("fuel_type", message.fuel_type);
				}
			});
	},

	vuna_vehicle_category(frm) {
		if (!frm.doc.vuna_vehicle_category) return;
		frappe.db
			.get_value("Vehicle Category", frm.doc.vuna_vehicle_category, "vehicle_model")
			.then(({ message }) => {
				if (message?.vehicle_model)
					frm.set_value("vuna_vehicle_model", message.vehicle_model);
			});
	},

	vuna_vehicle_owner(frm) {
		if (frm.doc.vuna_vehicle_owner === "Company" && frm.doc.vuna_supplier) {
			frm.set_value("vuna_supplier", null);
		}
	},
});

function render_operational_status(frm) {
	const field = frm.fields_dict.vuna_operational_status_html;
	if (!field) return;
	if (frm.is_new()) {
		field.$wrapper.html(
			`<div class="text-muted">${__(
				"Save the Vehicle to view its operational status."
			)}</div>`
		);
		return;
	}

	field.$wrapper.html(`<div class="text-muted">${__("Loading operational status...")}</div>`);
	frappe
		.call({
			method: "vuna_transport.services.fleet.fleet_service.get_vehicle_operational_status",
			args: { vehicle: frm.doc.name },
		})
		.then(({ message }) => {
			if (!message || frm.doc.name !== message.vehicle) return;
			frm.doc.vuna_operational_status = message.operational_state;
			frm.doc.vuna_dispatch_eligible = message.dispatch_eligible ? 1 : 0;
			frm.doc.vuna_status_updated_at = message.evaluated_at;
			frm.refresh_fields([
				"vuna_operational_status",
				"vuna_dispatch_eligible",
				"vuna_status_updated_at",
			]);
			field.$wrapper.html(get_operational_status_html(message));
			field.$wrapper.find(".vuna-refresh-vehicle-status").on("click", () => {
				render_operational_status(frm);
			});
		})
		.catch(() => {
			field.$wrapper.html(
				`<div class="text-muted">${__("Operational status could not be loaded.")}</div>`
			);
		});
}

function get_operational_status_html(status) {
	const escape = (value) => frappe.utils.escape_html(String(value ?? ""));
	const indicator = status.dispatch_eligible ? "green" : "orange";
	const reference = (doctype, name) =>
		name
			? `<a href="/app/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}">${escape(
					name
			  )}</a>`
			: `<span class="text-muted">${__("None")}</span>`;
	const next_available = status.next_available_at
		? frappe.datetime.str_to_user(status.next_available_at)
		: status.dispatch_eligible
		? __("Now")
		: __("Not determined");
	const blocker_details = (status.reasons || [])
		.map((reason) => {
			const linked =
				reason.reference_doctype && reason.reference_name
					? reference(reason.reference_doctype, reason.reference_name)
					: "";
			return `<li>${escape(reason.message)}${linked ? ` — ${linked}` : ""}</li>`;
		})
		.join("");

	return `<div class="vuna-vehicle-operational-status" style="border:1px solid var(--border-color);border-radius:var(--border-radius-md);padding:var(--padding-md);background:var(--control-bg);">
		<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;">
			<div>
				<span class="indicator-pill ${indicator}">${escape(status.operational_state)}</span>
				<span class="text-muted" style="margin-left:8px;">${
					status.dispatch_eligible
						? __("Dispatch eligible now")
						: __("Not dispatch eligible now")
				}</span>
			</div>
			<button type="button" class="btn btn-xs btn-default vuna-refresh-vehicle-status">${__(
				"Refresh Status"
			)}</button>
		</div>
		<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-top:14px;">
			<div><div class="text-muted small">${__("Linked Asset")}</div><div>${reference(
		"Asset",
		status.linked_asset
	)}</div></div>
			<div><div class="text-muted small">${__("Compliance")}</div><div>${escape(
		status.compliance_status || __("Not evaluated")
	)}</div></div>
			<div><div class="text-muted small">${__("Reason")}</div><div>${escape(
		status.primary_reason || __("No operational blockers")
	)}</div></div>
			<div><div class="text-muted small">${__("Current Allocation")}</div><div>${reference(
		"Vehicle Allocation",
		status.current_allocation
	)}</div></div>
			<div><div class="text-muted small">${__("Current Trip")}</div><div>${reference(
		"Delivery Trip",
		status.current_trip
	)}</div></div>
			<div><div class="text-muted small">${__("Next Available")}</div><div>${escape(
		next_available
	)}</div></div>
		</div>
		${
			blocker_details
				? `<details style="margin-top:12px;"><summary>${__(
						"View all blockers"
				  )}</summary><ul style="margin-top:8px;">${blocker_details}</ul></details>`
				: ""
		}
	</div>`;
}
