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
				default: frm.doc.route_return_to_start || frm.doc.vuna_route_return_to_start || 0,
				onchange: clear_result,
			},
			{
				fieldname: "optimize_stops",
				fieldtype: "Check",
				label: __("Optimize Stop Order"),
				default: 0,
				hidden:
					!context.can_optimize ||
					frm.doc.docstatus !== 0 ||
					(frm.doc.doctype === "Transport Load" &&
						(!["Customer Delivery", "Warehouse Transfer"].includes(
							frm.doc.movement_type
						) ||
							Boolean(frm.doc.active_vehicle_allocation || frm.doc.delivery_trip))),
				description: __(
					"Google suggests the full stop order. Return to Start adds a journey back to the source warehouse."
				),
				onchange: clear_result,
			},
			{ fieldname: "result", fieldtype: "HTML" },
		],
		primary_action_label: __("Preview Route"),
		secondary_action_label: ["Vehicle Allocation", "Delivery Trip"].includes(frm.doc.doctype)
			? __("Apply Route & Distance")
			: __("Apply Stop Order"),
		secondary_action() {
			if (
				!suggestion ||
				busy ||
				frm.is_dirty() ||
				frm.doc.modified !== suggestion.source_modified
			) {
				return frappe.msgprint(
					__("The document changed. Save and preview the route again.")
				);
			}
			if (!vuna_transport.road_routing.apply_stop_order(frm, suggestion)) return;
			dialog.hide();
			frappe.show_alert({
				message: ["Vehicle Allocation", "Delivery Trip"].includes(frm.doc.doctype)
					? __(
							"Route and distance applied. Save the document to calculate and retain costs."
					  )
					: __("Stop order applied. Save the document to keep it."),
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
				if (
					frm.doc.docstatus === 0 &&
					(message?.allocation_route_rows ||
						(message?.trip_stop_order && message?.route_preview_token) ||
						((message?.load_row_order || message?.trip_stop_order) &&
							values.optimize_stops))
				) {
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
	if (frm.doc.docstatus !== 0) return false;
	if (frm.doc.doctype === "Vehicle Allocation") {
		if (!result.allocation_route_rows?.length || !result.route_fingerprint) return false;
		frm.clear_table("route_stops");
		for (const row of result.allocation_route_rows) frm.add_child("route_stops", row);
		frm.set_value("route_manifest_fingerprint", result.route_fingerprint);
		frm.set_value("route_preview_token", result.route_preview_token || null);
		frm.set_value("route_source_warehouse", result.source_warehouse);
		frm.set_value("route_return_to_start", result.return_to_start || 0);
		frm.set_value("route_status", "Proposed");
		frm.refresh_field("route_stops");
		frm.__road_preview_inputs = vuna_transport.road_routing.preview_inputs(frm);
		frm.dirty();
		return true;
	}
	if (frm.doc.doctype === "Delivery Trip") {
		const rows = frm.doc.delivery_stops || [];
		const names = result.trip_stop_order || [];
		if (
			!names.length ||
			names.length !== rows.length ||
			new Set(names).size !== rows.length ||
			!names.every((name) => rows.some((row) => row.name === name))
		) {
			frappe.msgprint(
				__("This stop order no longer matches the draft trip. Preview it again.")
			);
			return false;
		}
		const by_name = new Map(rows.map((row) => [row.name, row]));
		frm.doc.delivery_stops = names.map((name, index) =>
			Object.assign(by_name.get(name), { idx: index + 1 })
		);
		frm.set_value("vuna_route_return_to_start", result.return_to_start || 0);
		frm.set_value("vuna_route_preview_token", result.route_preview_token || null);
		frm.refresh_field("delivery_stops");
		frm.__trip_road_preview_inputs = vuna_transport.road_routing.trip_preview_inputs(frm);
		frm.dirty();
		return true;
	}
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
	const cost = result.cost_estimate;
	const retention_message =
		result.source_doctype === "Delivery Trip"
			? __(
					"Apply the route and save the trip to update its distance and costs. The allocation planning estimate is unchanged."
			  )
			: __(
					"Apply the route and save the allocation to retain its distance and calculated costs."
			  );
	const cost_html = cost
		? `<div class="border-top pt-2 mb-2"><strong>${__("Journey cost preview")}: ${number(
				cost.marginal_cost
		  )} ${escape(cost.currency || "")}</strong><p class="small text-muted">${__(
				"Shared trip charges are counted once. Monthly fixed costs are separate."
		  )} ${retention_message}</p>${(cost.warnings || [])
				.map((warning) => `<p class="small text-muted">${escape(warning)}</p>`)
				.join("")}</div>`
		: "";
	return `<div class="border rounded p-3"><div class="d-flex justify-content-between mb-3"><strong>${__(
		"{0} km · {1} driving hours",
		[number(result.distance_meters / 1000), number(result.duration_seconds / 3600)]
	)}</strong><span translate="no" style="font-size:12px;font-weight:400;white-space:nowrap">Google Maps</span></div><div class="table-responsive"><table class="table table-bordered table-sm"><thead><tr><th>#</th><th>${__(
		"From"
	)}</th><th>${__("To")}</th><th>${__("Road Distance")}</th><th>${__(
		"Driving Time"
	)}</th></tr></thead><tbody>${rows}</tbody></table></div>${cost_html}<ul class="small text-muted">${warnings}</ul></div>`;
};

vuna_transport.road_routing.trip_preview_inputs = function (frm) {
	return JSON.stringify([
		frm.doc.company,
		frm.doc.vehicle,
		frm.doc.driver,
		frm.doc.departure_time,
		frm.doc.vuna_route_return_to_start,
		frm.doc.vuna_vehicle_allocation,
		(frm.doc.delivery_stops || []).map((row) => [
			row.name,
			row.address,
			row.customer,
			row.delivery_note,
			row.vuna_delivery_load,
			row.vuna_task_keys,
		]),
	]);
};

vuna_transport.road_routing.preview_inputs = function (frm) {
	return JSON.stringify([
		frm.doc.company,
		frm.doc.vehicle,
		frm.doc.driver,
		frm.doc.cost_structure,
		frm.doc.planned_reporting_time,
		frm.doc.route_return_to_start,
		(frm.doc.delivery_loads || []).map((row) => row.delivery_load),
		(frm.doc.route_stops || []).map((row) => row.stop_key),
	]);
};
