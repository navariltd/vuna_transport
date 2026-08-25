frappe.listview_settings["Delivery Order"] = {
	add_fields: ["status", "required_delivery_date", "unallocated_quantity"],
	get_indicator(doc) {
		const colors = {
			Draft: [__("Draft"), "gray"],
			Open: [__("Open"), "blue"],
			"Partially Allocated": [__("Partially Allocated"), "orange"],
			Allocated: [__("Allocated"), "green"],
			Dispatched: [__("Dispatched"), "blue"],
			"Partially Delivered": [__("Partially Delivered"), "orange"],
			"Delivery Exception": [__("Delivery Exception"), "red"],
			Delivered: [__("Delivered"), "green"],
			Cancelled: [__("Cancelled"), "red"],
		};
		return colors[doc.status] || [__(doc.status), "gray"];
	},
	onload(listview) {
		listview.page.add_inner_button(__("Suggest Delivery Loads"), () => {
			open_load_suggestion_dialog(listview);
		}, __("Planning"));

		listview.page.add_action_item(__("Create"), () => {
			const names = listview.get_checked_items().map((row) => row.name);
			if (!names.length) {
				frappe.msgprint(__("Select at least one Delivery Order."));
				return;
			}

			frappe.call({
				method: "vuna_transport.services.planning.delivery_load_service.create_delivery_load",
				args: { delivery_order_names: names },
			}).then((response) => {
				if (response.message) {
					frappe.show_alert({ message: __("Delivery Load {0} created", [response.message]), indicator: "green" });
					frappe.set_route("Form", "Delivery Load", response.message);
				}
			});
		});
	},
};

async function open_load_suggestion_dialog(listview) {
	const selected_names = listview.get_checked_items().map((row) => row.name);
	const configured_count = await frappe.db.get_single_value(
		"Transport Settings",
		"load_suggestion_option_count",
	);
	const dialog = new frappe.ui.Dialog({
		title: __("Delivery Load Planner"),
		size: "extra-large",
		fields: [
			{
				fieldname: "company",
				fieldtype: "Link",
				options: "Company",
				label: __("Company"),
				reqd: 1,
				default: frappe.defaults.get_user_default("Company"),
			},
			{
				fieldname: "source_warehouse",
				fieldtype: "Link",
				options: "Warehouse",
				label: __("Source Warehouse"),
			},
			{ fieldtype: "Column Break" },
			{
				fieldname: "delivery_date",
				fieldtype: "Date",
				label: __("Required Delivery Date"),
			},
			{
				fieldname: "option_count",
				fieldtype: "Int",
				label: __("Configurations to Show"),
				default: Math.max(3, configured_count || 3),
				description: __("Recommended configuration plus alternative valid load plans (3–10)."),
			},
			{ fieldtype: "Section Break" },
			{
				fieldname: "selected_order_note",
				fieldtype: "HTML",
				options: selected_names.length
					? `<div class="alert alert-info">${__("Planning from {0} selected Delivery Order(s).", [selected_names.length])}</div>`
					: `<div class="text-muted">${__("No rows selected: all eligible orders matching these filters will be considered.")}</div>`,
			},
			{
				fieldname: "suggestion_results",
				fieldtype: "HTML",
			},
		],
		primary_action_label: __("Generate Suggestions"),
		primary_action(values) {
			if (values.option_count < 3 || values.option_count > 10) {
				frappe.msgprint(__("Configurations to Show must be between 3 and 10."));
				return;
			}
			generate_load_suggestions(dialog, values, selected_names);
		},
	});
	dialog.show();
}

function generate_load_suggestions(dialog, values, selected_names) {
	const wrapper = dialog.get_field("suggestion_results").$wrapper;
	wrapper.html(`<div class="text-muted">${__("Evaluating compatible orders and vehicle categories...")}</div>`);
	frappe.call({
		method: "vuna_transport.services.planning.load_suggestion_service.suggest_delivery_loads",
		args: {
			company: values.company,
			source_warehouse: values.source_warehouse,
			delivery_date: values.delivery_date,
			delivery_order_names: selected_names,
			option_count: values.option_count,
		},
		freeze: true,
		freeze_message: __("Building ranked load configurations..."),
	}).then(({ message }) => {
		const result = message || { suggestions: [], warnings: [] };
		render_load_suggestions(wrapper, result);
		wrapper.off("click", ".create-suggested-load");
		wrapper.on("click", ".create-suggested-load", function () {
			const suggestion = result.suggestions[Number($(this).attr("data-index"))];
			create_load_from_suggestion(dialog, suggestion);
		});
	});
}

function render_load_suggestions(wrapper, result) {
	const escape = frappe.utils.escape_html;
	const notices = (result.warnings || [])
		.map((warning) => `<div class="alert alert-warning" style="margin-bottom:8px;">${escape(warning)}</div>`)
		.join("");
	if (!result.suggestions?.length) {
		wrapper.html(`${notices}<div class="alert alert-info">${__("No valid load configurations were found. Review capacities, zones, dates, and unallocated quantities.")}</div>`);
		return;
	}

	const cards = result.suggestions.map((suggestion, index) => {
		const utilization = ["weight", "volume", "pallets", "cases", "crates"]
			.map((dimension) => {
				const value = suggestion.utilization[dimension];
				return value === null || value === undefined
					? ""
					: `<div><span class="text-muted">${__(frappe.unscrub(dimension))}</span><br><strong>${Number(value).toFixed(1)}%</strong></div>`;
			})
			.filter(Boolean)
			.join("");
		const orders = suggestion.delivery_orders
			.map((order) => `<a href="/app/delivery-order/${encodeURIComponent(order.name)}">${escape(order.name)}</a> — ${escape(order.customer_name || order.customer || "")}`)
			.join("<br>");
		const reasons = suggestion.positive_factors
			.map((reason) => `<li style="margin-bottom:4px;">✓ ${escape(reason)}</li>`)
			.join("");
		const warnings = suggestion.warnings
			.map((warning) => `<li style="margin-bottom:4px;">⚠ ${escape(warning)}</li>`)
			.join("");
		return `
			<div style="border:2px solid ${suggestion.recommended ? "var(--primary)" : "var(--border-color)"}; border-radius:10px; padding:16px; margin:14px 0; background:var(--card-bg);">
				<div style="display:flex; justify-content:space-between; gap:16px; align-items:start;">
					<div>
						<div class="text-muted">${suggestion.recommended ? __("Recommended Configuration") : __("Alternative Configuration {0}", [suggestion.rank - 1])}</div>
						<h4 style="margin:4px 0;">${escape(suggestion.vehicle_category_label)}</h4>
						<div>${__("{0} order(s) · {1} stop(s)", [suggestion.delivery_orders.length, suggestion.total_stops])}</div>
						<div class="text-muted small">${escape(suggestion.source_warehouse || "")} · ${frappe.datetime.str_to_user(suggestion.delivery_date)}</div>
					</div>
					<div style="text-align:right;"><div class="text-muted">${__("Overall Score")}</div><div style="font-size:26px; font-weight:700;">${Number(suggestion.score).toFixed(0)}<span style="font-size:14px;">/100</span></div></div>
				</div>
				<div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(90px,1fr)); gap:10px; padding:12px 0; margin:12px 0; border-top:1px solid var(--border-color); border-bottom:1px solid var(--border-color);">${utilization}</div>
				<details>
					<summary style="cursor:pointer; font-weight:600;">${__("Review orders and reasoning")}</summary>
					<div style="display:grid; grid-template-columns:1fr 1fr; gap:18px; margin-top:12px;">
						<div><strong>${__("Included Delivery Orders")}</strong><div style="margin-top:6px;">${orders}</div></div>
						<div><strong>${__("Why this works")}</strong><ul style="list-style:none; padding:0; margin-top:6px;">${reasons}${warnings}</ul></div>
					</div>
				</details>
				<div style="text-align:right; margin-top:14px;"><button class="btn ${suggestion.recommended ? "btn-primary" : "btn-default"} create-suggested-load" data-index="${index}">${__("Create Delivery Load")}</button></div>
			</div>`;
	}).join("");
	wrapper.html(`${notices}${cards}`);
}

function create_load_from_suggestion(dialog, suggestion) {
	frappe.call({
		method: "vuna_transport.services.planning.load_suggestion_service.create_delivery_load_from_suggestion",
		args: { suggestion },
		freeze: true,
		freeze_message: __("Revalidating and creating Delivery Load..."),
	}).then(({ message }) => {
		if (!message) return;
		dialog.hide();
		frappe.show_alert({ message: __("Delivery Load {0} created", [message]), indicator: "green" });
		frappe.set_route("Form", "Delivery Load", message);
	});
}
