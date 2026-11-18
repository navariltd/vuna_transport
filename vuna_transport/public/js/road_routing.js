frappe.provide("vuna_transport.road_routing");

vuna_transport.road_routing.show_preview = function (frm, context = {}) {
	if (frm.is_new() || frm.is_dirty())
		return frappe.msgprint(__("Save your changes before previewing a road route."));
	let busy = false;
	let closed = false;
	let dialog;
	let suggestion = null;
	const clear_result = () => {
		suggestion = null;
		dialog?.fields_dict.result?.$wrapper.empty();
		dialog?.get_secondary_btn().hide();
	};
	dialog = new frappe.ui.Dialog({
		title: __("Road Route Preview"),
		size: "large",
		fields: [
			{
				fieldname: "return_to_start",
				fieldtype: "Check",
				label: __("Return to Start"),
				default: 0,
				onchange: clear_result,
			},
			{
				fieldname: "optimize_stops",
				fieldtype: "Check",
				label: __("Optimize Stop Order"),
				default: 0,
				hidden:
					!context.can_optimize ||
					frm.doc.doctype !== "Transport Load" ||
					frm.doc.docstatus !== 0 ||
					!["Customer Delivery", "Warehouse Transfer"].includes(frm.doc.movement_type) ||
					Boolean(frm.doc.active_vehicle_allocation || frm.doc.delivery_trip),
				description: __(
					"Google suggests the full stop order. Return to Start adds a journey back to the source warehouse."
				),
				onchange: clear_result,
			},
			{ fieldname: "result", fieldtype: "HTML" },
		],
		primary_action_label: __("Preview Route"),
		secondary_action_label: __("Apply Stop Order"),
		secondary_action() {
			if (
				!suggestion ||
				busy ||
				frm.is_dirty() ||
				frm.doc.modified !== suggestion.source_modified
			) {
				return frappe.msgprint(__("The load changed. Save and preview the route again."));
			}
			if (!vuna_transport.road_routing.apply_stop_order(frm, suggestion)) return;
			dialog.hide();
			frappe.show_alert({
				message: __("Stop order applied. Save the load to keep it."),
				indicator: "green",
			});
		},
		onhide: () => {
			closed = true;
			clear_result();
		},
		async primary_action(values) {
			if (busy) return;
			if (frm.is_dirty())
				return frappe.msgprint(__("Save your changes before previewing a road route."));
			busy = true;
			clear_result();
			dialog.get_primary_btn().prop("disabled", true);
			dialog.fields_dict.result.$wrapper.html(
				`<p class="text-muted" role="status">${__("Calculating road route…")}</p>`
			);
			try {
				const { message } = await frappe.call({
					method: "vuna_transport.services.routing.road_routing_service.preview_road_route",
					args: {
						doctype: frm.doc.doctype,
						name: frm.doc.name,
						return_to_start: values.return_to_start ? 1 : 0,
						optimize_stops: values.optimize_stops ? 1 : 0,
					},
				});
				if (closed) return;
				if (
					frm.is_dirty() ||
					Boolean(values.return_to_start) !==
						Boolean(dialog.get_value("return_to_start")) ||
					Boolean(values.optimize_stops) !== Boolean(dialog.get_value("optimize_stops"))
				) {
					dialog.fields_dict.result.$wrapper.html(
						`<p>${__(
							"The route inputs changed. Save any document changes and preview again."
						)}</p>`
					);
					return;
				}
				dialog.fields_dict.result.$wrapper.html(
					vuna_transport.road_routing.render_result(message)
				);
				if (message?.load_row_order && values.optimize_stops) {
					suggestion = message;
					dialog.get_secondary_btn().show();
				}
			} catch {
				if (closed) return;
				dialog.fields_dict.result.$wrapper.html(
					`<p class="text-muted">${__(
						"No route preview was produced. Check the message above and try again; transport records are unchanged."
					)}</p>`
				);
			} finally {
				busy = false;
				dialog.get_primary_btn().prop("disabled", false);
			}
		},
	});
	dialog.show();
	dialog.get_secondary_btn().hide();
};

vuna_transport.road_routing.apply_stop_order = function (frm, result) {
	const rows = frm.doc.delivery_orders || [];
	const names = result.load_row_order || [];
	if (
		frm.doc.doctype !== "Transport Load" ||
		frm.doc.docstatus !== 0 ||
		!names.length ||
		names.length !== rows.length ||
		new Set(names).size !== rows.length ||
		!names.every((name) => rows.some((row) => row.name === name))
	) {
		frappe.msgprint(__("This stop order no longer matches the draft load. Preview it again."));
		return false;
	}
	const by_name = new Map(rows.map((row) => [row.name, row]));
	const stops = new Map();
	frm.doc.delivery_orders = names.map((name, index) => {
		const row = by_name.get(name);
		const destination =
			frm.doc.movement_type === "Warehouse Transfer"
				? row.destination_warehouse
				: JSON.stringify([row.customer, row.delivery_address]);
		if (!stops.has(destination)) stops.set(destination, stops.size + 1);
		row.idx = index + 1;
		row.stop_sequence = stops.get(destination);
		return row;
	});
	frm.refresh_field("delivery_orders");
	frm.dirty();
	return true;
};

vuna_transport.road_routing.render_result = function (result) {
	if (!result) return "";
	const escape = frappe.utils.escape_html;
	const number = (value) => escape(format_number(value, null, 2));
	const rows = (result.legs || [])
		.map(
			(leg, index) =>
				`<tr><td>${index + 1}</td><td>${escape(leg.from_label)}</td><td>${escape(
					leg.to_label
				)}</td><td>${number(leg.distance_meters / 1000)} km</td><td>${number(
					leg.duration_seconds / 60
				)} ${__("min")}</td></tr>`
		)
		.join("");
	const warnings = (result.warnings || [])
		.map((message) => `<li>${escape(message)}</li>`)
		.join("");
	return `<div class="border rounded p-3"><div class="d-flex justify-content-between mb-3"><strong>${__(
		"{0} km · {1} driving hours",
		[number(result.distance_meters / 1000), number(result.duration_seconds / 3600)]
	)}</strong><span translate="no" style="font-size:12px;font-weight:400;white-space:nowrap">Google Maps</span></div><div class="table-responsive"><table class="table table-bordered table-sm"><thead><tr><th>#</th><th>${__(
		"From"
	)}</th><th>${__("To")}</th><th>${__("Road Distance")}</th><th>${__(
		"Driving Time"
	)}</th></tr></thead><tbody>${rows}</tbody></table></div><ul class="small text-muted">${warnings}</ul></div>`;
};
