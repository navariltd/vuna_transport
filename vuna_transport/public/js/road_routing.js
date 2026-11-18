frappe.provide("vuna_transport.road_routing");

vuna_transport.road_routing.show_preview = function (frm) {
	if (frm.is_new() || frm.is_dirty())
		return frappe.msgprint(__("Save your changes before previewing a road route."));
	let busy = false;
	let closed = false;
	let dialog;
	dialog = new frappe.ui.Dialog({
		title: __("Road Route Preview"),
		size: "large",
		fields: [
			{
				fieldname: "return_to_start",
				fieldtype: "Check",
				label: __("Return to Start"),
				default: 0,
				onchange: () => dialog?.fields_dict.result?.$wrapper.empty(),
			},
			{ fieldname: "result", fieldtype: "HTML" },
		],
		primary_action_label: __("Preview Route"),
		onhide: () => {
			closed = true;
			dialog?.fields_dict.result?.$wrapper.empty();
		},
		async primary_action(values) {
			if (busy) return;
			if (frm.is_dirty())
				return frappe.msgprint(__("Save your changes before previewing a road route."));
			busy = true;
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
					},
				});
				if (closed) return;
				if (
					frm.is_dirty() ||
					Boolean(values.return_to_start) !==
						Boolean(dialog.get_value("return_to_start"))
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
