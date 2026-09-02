frappe.provide("vuna_transport.DispatchPlanning");

frappe.pages["dispatch-planning"].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Dispatch Planning"),
		single_column: true,
	});
	wrapper.dispatch_planning = new vuna_transport.DispatchPlanning.Controller(wrapper);
};

frappe.pages["dispatch-planning"].on_page_show = function (wrapper) {
	wrapper.dispatch_planning?.refresh();
};

vuna_transport.DispatchPlanning.Controller = class DispatchPlanningController {
	constructor(wrapper) {
		this.wrapper = wrapper;
		this.page = wrapper.page;
		this.start = 0;
		this.page_length = 30;
		this.orders = [];
		this.details = {};
		this.allocations = new Map();
		this.plan = null;
		this.suggestions = [];
		this.make_layout();
		this.make_filters();
		this.bind_events();
	}

	make_layout() {
		this.$root = $(
			`<div class="dispatch-planning">
				<div class="dp-toolbar">
					<div class="dp-filter-grid"></div>
					<div class="dp-toolbar-actions">
						<button class="btn btn-default btn-refresh">${__("Refresh")}</button>
						<button class="btn btn-default btn-suggest">${__("Suggest Loads")}</button>
					</div>
				</div>
				<div class="dp-section dp-warehouse-section">
					<div class="dp-section-title">${__("Warehouse Demand")}</div>
					<div class="dp-warehouse-cards"></div>
				</div>
				<div class="dp-grid">
					<div class="dp-main">
						<div class="dp-section">
							<div class="dp-section-heading">
								<div class="dp-section-title">${__("Delivery Demand")}</div>
								<div class="dp-pagination"></div>
							</div>
							<div class="dp-demand"></div>
						</div>
						<div class="dp-section dp-suggestions-section">
							<div class="dp-section-title">${__("Load Suggestions")}</div>
							<div class="dp-suggestions dp-empty">${__(
								"Run suggestions for the current filters or selected demand."
							)}</div>
						</div>
					</div>
					<div class="dp-sidebar">
						<div class="dp-section dp-plan">
							<div class="dp-section-title">${__("Manual Load Plan")}</div>
							<div class="dp-plan-content dp-empty">${__("Select Delivery Orders to begin planning.")}</div>
							<div class="dp-plan-actions">
								<button class="btn btn-default btn-sm btn-evaluate" disabled>${__("Evaluate")}</button>
								<button class="btn btn-primary btn-sm btn-create-load" disabled>${__(
									"Create Delivery Load"
								)}</button>
							</div>
						</div>
						<div class="dp-section">
							<div class="dp-section-title">${__(
								"Fleet Availability"
							)} <small class="text-muted dp-availability-date"></small></div>
							<div class="dp-fleet dp-empty">${__("Choose a company.")}</div>
						</div>
						<div class="dp-section">
							<div class="dp-section-title">${__("Driver Availability")}</div>
							<div class="dp-drivers dp-empty">${__("Choose a company.")}</div>
						</div>
					</div>
				</div>
			</div>`
		).appendTo(this.page.main);
	}

	make_filters() {
		const definitions = [
			["company", "Link", __("Company"), "Company", frappe.defaults.get_default("company")],
			["required_delivery_date_from", "Date", __("Delivery Date From")],
			["required_delivery_date_to", "Date", __("Delivery Date To")],
			["source_warehouse", "Link", __("Source Warehouse"), "Warehouse"],
			["transport_zone", "Link", __("Transport Zone"), "Transport Zone"],
			["delivery_method", "Link", __("Delivery Method"), "Delivery Method"],
			["priority", "Select", __("Priority"), "\nLow\nMedium\nHigh\nUrgent"],
			["customer", "Link", __("Customer"), "Customer"],
			[
				"allocation_state",
				"Select",
				__("Allocation State"),
				"\nUnallocated\nPartially Allocated",
			],
		];
		this.filters = {};
		for (const [fieldname, fieldtype, label, options, default_value] of definitions) {
			const field = frappe.ui.form.make_control({
				parent: this.$root.find(".dp-filter-grid"),
				df: {
					fieldname,
					fieldtype,
					label,
					options,
					default: default_value,
					onchange: () => this.filters_changed(),
				},
				render_input: true,
			});
			field.set_value(default_value || "");
			this.filters[fieldname] = field;
		}
	}

	bind_events() {
		this.$root.on("click", ".btn-refresh", () => this.refresh(true));
		this.$root.on("click", ".btn-suggest", () => this.load_suggestions());
		this.$root.on("click", ".btn-evaluate", () => this.evaluate_plan());
		this.$root.on("click", ".btn-create-load", () => this.create_load());
		this.$root.on("click", ".btn-prev", () => this.change_page(-1));
		this.$root.on("click", ".btn-next", () => this.change_page(1));
		this.$root.on("change", ".dp-order-select", (event) => this.toggle_order(event));
		this.$root.on("click", ".dp-expand-order", (event) => this.toggle_details(event));
		this.$root.on("change", ".dp-allocation-qty", (event) => this.change_quantity(event));
		this.$root.on("click", ".dp-use-suggestion", (event) => this.use_suggestion(event));
		this.$root.on("click", ".dp-create-suggestion", (event) =>
			this.create_from_suggestion(event)
		);
		this.$root.on("click", ".dp-fleet-category", (event) => this.show_vehicle_details(event));
		this.$root.on("click", ".dp-driver-details", () => this.show_driver_details());
	}

	filters_changed() {
		this.start = 0;
		this.clear_suggestions();
		clearTimeout(this.filter_timer);
		this.filter_timer = setTimeout(() => this.refresh(true), 300);
	}

	get_filter_values() {
		return Object.fromEntries(
			Object.entries(this.filters).map(([key, field]) => [key, field.get_value()])
		);
	}

	planning_window() {
		const filter_from = this.filters.required_delivery_date_from.get_value();
		const filter_to = this.filters.required_delivery_date_to.get_value();
		const date_from = filter_from && filter_from !== "undefined" ? filter_from : null;
		const date_to = filter_to && filter_to !== "undefined" ? filter_to : null;
		const fallback = frappe.datetime.get_today();
		const start_date = date_from || date_to || fallback;
		const end_date = date_to || date_from || fallback;
		if (start_date > end_date) return null;
		return {
			from_time: `${start_date} 00:00:00`,
			to_time: `${frappe.datetime.add_days(end_date, 1)} 00:00:00`,
		};
	}

	async refresh(clear_selection = false) {
		const filters = this.get_filter_values();
		if (!filters.company) return;
		this.clear_suggestions();
		if (clear_selection) this.clear_plan();
		this.$root.find(".dp-demand").html(this.loading());
		const args = { ...filters, start: this.start, page_length: this.page_length };
		try {
			const [demand] = await Promise.all([
				this.call("get_planning_demand", args),
				this.refresh_availability(),
			]);
			this.orders = demand.orders || [];
			this.has_more = demand.has_more;
			this.render_warehouses(demand.warehouse_summaries || [], demand.summary_truncated);
			this.render_demand();
		} catch (error) {
			this.$root.find(".dp-demand").html(this.error_message(error));
		}
	}

	async refresh_availability() {
		const { company } = this.get_filter_values();
		if (!company) return;
		const window = this.planning_window();
		if (!window) {
			const message = __("Delivery Date From cannot be after Delivery Date To.");
			this.$root.find(".dp-fleet, .dp-drivers").addClass("dp-empty").html(message);
			return;
		}
		const filter_from = this.filters.required_delivery_date_from.get_value();
		const filter_to = this.filters.required_delivery_date_to.get_value();
		const date_from = filter_from && filter_from !== "undefined" ? filter_from : null;
		const date_to = filter_to && filter_to !== "undefined" ? filter_to : null;
		const fallback = frappe.datetime.get_today();
		const start_date = date_from || date_to || fallback;
		const end_date = date_to || date_from || fallback;
		const availability_label =
			start_date === end_date
				? frappe.datetime.str_to_user(start_date)
				: `${frappe.datetime.str_to_user(start_date)} – ${frappe.datetime.str_to_user(
						end_date
				  )}`;
		this.$root
			.find(".dp-availability-date")
			.text(
				date_from || date_to
					? `— ${availability_label}`
					: `— ${__("Today")}, ${availability_label}`
			);
		const [fleet, drivers] = await Promise.all([
			this.call("get_fleet_availability_summary", { company, ...window }),
			this.call("get_driver_availability_summary", { company, ...window }),
		]);
		this.render_fleet(fleet);
		this.render_drivers(drivers);
	}

	render_warehouses(rows, truncated) {
		const html = rows.length
			? rows
					.map(
						(row) => `<button class="dp-summary-card" data-warehouse="${this.escape(
							row.source_warehouse
						)}">
				<strong>${this.escape(row.source_warehouse || __("No Warehouse"))}</strong>
				<span>${row.delivery_order_count} ${__("orders")}</span>
				<span>${this.number(row.weight)} ${__("weight")} · ${this.number(row.volume)} ${__(
							"volume"
						)}</span>
				<span>${this.number(row.unallocated_quantity)} ${__("remaining")}</span>
			</button>`
					)
					.join("")
			: `<div class="dp-empty">${__(
					"No open delivery demand matches these filters."
			  )}</div>`;
		this.$root
			.find(".dp-warehouse-cards")
			.html(
				html +
					(truncated
						? `<div class="text-muted small">${__(
								"Summary limited to the first 500 orders."
						  )}</div>`
						: "")
			);
		this.$root.find(".dp-summary-card").on("click", (event) => {
			this.filters.source_warehouse.set_value($(event.currentTarget).data("warehouse"));
		});
	}

	render_demand() {
		if (!this.orders.length) {
			this.$root
				.find(".dp-demand")
				.html(`<div class="dp-empty">${__("No open Delivery Orders found.")}</div>`);
			this.render_pagination();
			return;
		}
		const rows = this.orders
			.map((order) => {
				const selected = this.order_selected(order.name) ? "checked" : "";
				const requirements =
					[
						order.refrigeration_required ? __("Refrigerated") : "",
						order.tail_lift_required ? __("Tail lift") : "",
						order.transport_handling_profile || "",
					]
						.filter(Boolean)
						.join(", ") || "—";
				return `<div class="dp-demand-row" data-order="${this.escape(order.name)}">
				<div><input type="checkbox" class="dp-order-select" ${selected}></div>
				<div><a href="/app/delivery-order/${encodeURIComponent(order.name)}"><strong>${this.escape(
					order.name
				)}</strong></a><small>${this.escape(
					order.customer_name || order.customer
				)}</small></div>
				<div><span>${this.escape(order.source_warehouse)}</span><small>${this.escape(
					order.transport_zone || __("No zone")
				)}</small></div>
				<div><span>${frappe.datetime.str_to_user(order.required_delivery_date)}</span><small>${this.escape(
					order.priority || __("Normal")
				)}</small></div>
				<div><span>${this.number(order.weight)} ${this.escape(
					order.weight_uom || ""
				)}</span><small>${this.number(order.volume)} ${this.escape(
					order.volume_uom || ""
				)}</small></div>
				<div><span>${this.number(order.allocated_quantity)} / ${this.number(
					order.requested_quantity
				)}</span><small>${this.number(order.unallocated_quantity)} ${__(
					"remaining"
				)}</small></div>
				<div title="${this.escape(requirements)}">${this.escape(requirements)}</div>
				<div><button class="btn btn-xs btn-default dp-expand-order">${__("Items")}</button></div>
			</div><div class="dp-item-details" data-details="${this.escape(order.name)}"></div>`;
			})
			.join("");
		this.$root
			.find(".dp-demand")
			.html(
				`<div class="dp-demand-header"><span></span><span>${__(
					"Order / Customer"
				)}</span><span>${__("Warehouse / Zone")}</span><span>${__(
					"Date / Priority"
				)}</span><span>${__("Weight / Volume")}</span><span>${__(
					"Allocated / Requested"
				)}</span><span>${__("Requirements")}</span><span></span></div>${rows}`
			);
		this.render_pagination();
	}

	render_pagination() {
		const page = Math.floor(this.start / this.page_length) + 1;
		this.$root
			.find(".dp-pagination")
			.html(
				`<button class="btn btn-xs btn-default btn-prev" ${
					this.start ? "" : "disabled"
				}>${__("Previous")}</button><span>${__("Page {0}", [
					page,
				])}</span><button class="btn btn-xs btn-default btn-next" ${
					this.has_more ? "" : "disabled"
				}>${__("Next")}</button>`
			);
	}

	change_page(direction) {
		this.start = Math.max(0, this.start + direction * this.page_length);
		this.refresh(false);
	}

	async toggle_order(event) {
		const $row = $(event.currentTarget).closest(".dp-demand-row");
		const name = $row.data("order");
		if (event.currentTarget.checked) {
			await this.ensure_details([name]);
			for (const item of this.details[name].items) {
				if (item.available_quantity > 0)
					this.allocations.set(item.name, {
						delivery_order: name,
						delivery_order_item: item.name,
						allocated_quantity: item.available_quantity,
					});
			}
		} else {
			for (const [key, row] of this.allocations)
				if (row.delivery_order === name) this.allocations.delete(key);
		}
		this.plan = null;
		this.clear_suggestions();
		this.render_plan();
	}

	async toggle_details(event) {
		const name = $(event.currentTarget).closest(".dp-demand-row").data("order");
		const $target = this.$root.find(`[data-details="${this.selector(name)}"]`);
		if ($target.children().length) return $target.toggle();
		$target.html(this.loading()).show();
		await this.ensure_details([name]);
		$target.html(this.render_item_table(this.details[name]));
	}

	async ensure_details(names) {
		const missing = names.filter((name) => !this.details[name]);
		if (!missing.length) return;
		const rows = await this.call("get_delivery_order_details", {
			delivery_order_names: missing,
		});
		for (const row of rows) this.details[row.name] = row;
	}

	render_item_table(order) {
		const restrictions = [
			order.maximum_vehicle_category
				? `${__("Maximum vehicle")}: ${order.maximum_vehicle_category}`
				: "",
			order.restricted_access ? __("Restricted access") : "",
			order.access_notes || "",
		]
			.filter(Boolean)
			.join(" · ");
		return `<div class="dp-items"><div class="dp-item-heading">${this.escape(
			restrictions || __("No additional access restrictions")
		)}</div>${order.items
			.map((item) => {
				const allocation = this.allocations.get(item.name);
				return `<div class="dp-item-row"><span><strong>${this.escape(
					item.item_code
				)}</strong><small>${this.escape(
					item.item_name || ""
				)}</small></span><span>${this.number(item.transport_quantity)} ${this.escape(
					item.stock_uom || ""
				)}<small>${this.number(item.allocated_quantity)} ${__(
					"allocated"
				)}</small></span><span>${this.number(item.available_quantity)} ${__(
					"available"
				)}</span><span>${this.number(item.weight)} / ${this.number(
					item.volume
				)}<small>${this.escape(
					item.handling_requirements || __("No handling profile")
				)}</small></span><span><input type="number" class="form-control input-xs dp-allocation-qty" data-order="${this.escape(
					order.name
				)}" data-item="${this.escape(item.name)}" min="0" max="${
					item.available_quantity
				}" step="any" value="${allocation?.allocated_quantity || 0}"></span></div>`;
			})
			.join("")}</div>`;
	}

	change_quantity(event) {
		const $input = $(event.currentTarget);
		const item = $input.data("item");
		const quantity = Math.max(0, flt($input.val()));
		if (quantity)
			this.allocations.set(item, {
				delivery_order: $input.data("order"),
				delivery_order_item: item,
				allocated_quantity: quantity,
			});
		else this.allocations.delete(item);
		this.plan = null;
		this.clear_suggestions();
		this.render_plan();
	}

	render_plan() {
		const rows = [...this.allocations.values()];
		const $content = this.$root.find(".dp-plan-content");
		this.$root.find(".btn-evaluate").prop("disabled", !rows.length);
		this.$root.find(".btn-create-load").prop("disabled", !this.plan);
		if (!rows.length)
			return $content
				.addClass("dp-empty")
				.html(__("Select Delivery Orders to begin planning."));
		if (!this.plan)
			return $content
				.removeClass("dp-empty")
				.html(
					`<strong>${__("{0} item allocations selected", [
						rows.length,
					])}</strong><p class="text-muted">${__(
						"Evaluate to recalculate capacity and compatible categories on the server."
					)}</p>`
				);
		const totals = this.plan.totals;
		const options = this.plan.compatible_vehicle_categories
			.map(
				(category) =>
					`<option value="${this.escape(category.name)}" ${
						category.recommended ? "selected" : ""
					}>${this.escape(category.label)}${
						category.recommended ? ` — ${__("Recommended")}` : ""
					}</option>`
			)
			.join("");
		$content
			.removeClass("dp-empty")
			.html(
				`<div class="dp-plan-metrics"><span><strong>${this.plan.total_stops}</strong>${__(
					"Stops"
				)}</span><span><strong>${this.number(totals.weight)}</strong>${this.escape(
					this.plan.weight_uom || __("Weight")
				)}</span><span><strong>${this.number(totals.volume)}</strong>${this.escape(
					this.plan.volume_uom || __("Volume")
				)}</span><span><strong>${this.number(totals.pallets)}</strong>${__(
					"Pallets"
				)}</span></div><label>${__(
					"Vehicle Category"
				)}</label><select class="form-control dp-category-select"><option value="">${__(
					"Select"
				)}</option>${options}</select><div class="small text-muted dp-requirements">${this.escape(
					this.requirements_text(this.plan.requirements)
				)}</div>`
			);
		this.$root.find(".btn-create-load").prop("disabled", !options);
	}

	async evaluate_plan() {
		this.plan = await this.call("evaluate_manual_load_plan", {
			allocations: [...this.allocations.values()],
			vehicle_category: this.$root.find(".dp-category-select").val() || null,
		});
		this.render_plan();
	}

	async create_load() {
		if (!this.plan) return;
		const vehicle_category =
			this.$root.find(".dp-category-select").val() || this.plan.recommended_vehicle_category;
		const name = await this.call(
			"create_delivery_load_from_plan",
			{ allocations: [...this.allocations.values()], vehicle_category },
			true
		);
		frappe.show_alert({
			message: __("Delivery Load {0} created", [name]),
			indicator: "green",
		});
		frappe.set_route("Form", "Delivery Load", name);
	}

	async load_suggestions() {
		const filters = this.get_filter_values();
		if (!filters.company) return;
		this.$root.find(".dp-suggestions").html(this.loading());
		const selected_names = [
			...new Set([...this.allocations.values()].map((row) => row.delivery_order)),
		];
		const response = await frappe.call({
			method: "vuna_transport.services.planning.load_suggestion_service.suggest_delivery_loads",
			args: {
				company: filters.company,
				source_warehouse: filters.source_warehouse || null,
				delivery_date_from: filters.required_delivery_date_from || null,
				delivery_date_to: filters.required_delivery_date_to || null,
				delivery_order_names: selected_names.length ? selected_names : null,
			},
			freeze: true,
			freeze_message: __("Building load suggestions..."),
		});
		const result = response.message || {};
		this.suggestions = result.suggestions || [];
		this.render_suggestions(result);
	}

	render_suggestions(result) {
		const $target = this.$root.find(".dp-suggestions");
		if (!this.suggestions.length)
			return $target
				.addClass("dp-empty")
				.html(
					this.escape(
						(result.warnings || [__("No compatible suggestions found.")]).join(" ")
					)
				);
		$target
			.removeClass("dp-empty")
			.html(
				this.suggestions
					.map((suggestion, index) => this.render_suggestion_card(suggestion, index))
					.join("") +
					(result.excluded_reasons?.length
						? `<div class="dp-exclusions"><strong>${__(
								"Other exclusions"
						  )}</strong>${result.excluded_reasons
								.map((reason) => `<div>${this.escape(reason)}</div>`)
								.join("")}</div>`
						: "")
			);
	}

	render_suggestion_card(suggestion, index) {
		const orders = suggestion.delivery_orders || [];
		const utilization = Object.entries(suggestion.utilization || {})
			.filter(([, value]) => value !== null && value !== undefined)
			.map(
				([dimension, value]) =>
					`<span><strong>${this.number(value)}%</strong>${this.escape(
						__(frappe.utils.to_title_case(dimension))
					)}</span>`
			)
			.join("");
		const order_rows = orders
			.map((order) => {
				const allocations = (suggestion.allocations || []).filter(
					(row) => row.delivery_order === order.name
				);
				const partial = allocations.some((row) => row.partial);
				return `<div class="dp-suggestion-order">
				<span><a href="/app/delivery-order/${encodeURIComponent(order.name)}"><strong>${this.escape(
					order.name
				)}</strong></a><small>${this.escape(
					order.customer_name || order.customer || ""
				)}</small></span>
				<span>${this.escape(order.delivery_address || __("No delivery address"))}</span>
				<span>${allocations.length} ${__("item row(s)")}${
					partial
						? `<small class="text-warning">${__("Partial allocation")}</small>`
						: ""
				}</span>
			</div>`;
			})
			.join("");
		const reasons = this.render_reason_list(
			suggestion.positive_factors,
			"green",
			__("Why these orders were grouped")
		);
		const warnings = this.render_reason_list(suggestion.warnings, "orange", __("Warnings"));
		return `<div class="dp-suggestion-card">
			<div><strong>#${suggestion.rank} · ${this.escape(
			suggestion.vehicle_category_label || suggestion.vehicle_category
		)}</strong><span class="indicator-pill ${suggestion.recommended ? "green" : "blue"}">${
			suggestion.score
		}/100</span></div>
			<div>${orders.length} ${__("orders")} · ${suggestion.total_stops || 0} ${__("stops")}</div>
			<div class="small text-muted">${this.escape(
				(suggestion.positive_factors || []).slice(0, 3).join(" · ")
			)}</div>
			<details class="dp-suggestion-review">
				<summary>${__("Review grouping and included orders")}</summary>
				<div class="dp-suggestion-review-body">
					<div class="dp-utilization">${
						utilization || `<span>${__("Capacity utilization unavailable")}</span>`
					}</div>
					<div class="dp-suggestion-orders"><div class="dp-suggestion-order dp-suggestion-order-header"><span>${__(
						"Delivery Order"
					)}</span><span>${__("Destination")}</span><span>${__(
			"Allocation"
		)}</span></div>${order_rows}</div>
					${reasons}${warnings}
				</div>
			</details>
			<div class="dp-card-actions"><button class="btn btn-xs btn-default dp-use-suggestion" data-index="${index}">${__(
			"Use as Plan"
		)}</button><button class="btn btn-xs btn-primary dp-create-suggestion" data-index="${index}">${__(
			"Create Load"
		)}</button></div>
		</div>`;
	}

	render_reason_list(items, indicator, title) {
		if (!items?.length) return "";
		return `<div class="dp-reason-list"><strong>${this.escape(title)}</strong>${items
			.map(
				(item) =>
					`<div><span class="indicator ${indicator}"></span>${this.escape(item)}</div>`
			)
			.join("")}</div>`;
	}

	async use_suggestion(event) {
		const suggestion = this.suggestions[cint($(event.currentTarget).data("index"))];
		this.allocations.clear();
		for (const row of suggestion.allocations || [])
			this.allocations.set(row.delivery_order_item, {
				delivery_order: row.delivery_order,
				delivery_order_item: row.delivery_order_item,
				allocated_quantity: row.allocated_quantity,
			});
		this.plan = null;
		await this.ensure_details([
			...new Set([...this.allocations.values()].map((row) => row.delivery_order)),
		]);
		this.render_demand();
		await this.evaluate_plan();
		this.$root.find(".dp-plan")[0].scrollIntoView({ behavior: "smooth", block: "start" });
	}

	async create_from_suggestion(event) {
		const suggestion = this.suggestions[cint($(event.currentTarget).data("index"))];
		const response = await frappe.call({
			method: "vuna_transport.services.planning.load_suggestion_service.create_delivery_load_from_suggestion",
			args: { suggestion },
			freeze: true,
			freeze_message: __("Revalidating and creating Delivery Load..."),
		});
		frappe.set_route("Form", "Delivery Load", response.message);
	}

	render_fleet(data) {
		const rows = data.categories || [];
		this.$root
			.find(".dp-fleet")
			.toggleClass("dp-empty", !rows.length)
			.html(
				rows.length
					? rows
							.map(
								(row) =>
									`<button class="dp-availability-row dp-fleet-category" data-category="${this.escape(
										row.vehicle_category
									)}"><strong>${this.escape(
										row.vehicle_category_label || row.vehicle_category
									)}</strong><span>${row.available}/${row.total} ${__(
										"available"
									)}</span><small>${row.allocated} ${__("allocated")} · ${
										row.in_trip
									} ${__("in trip")} · ${row.under_maintenance} ${__(
										"maintenance"
									)} · ${row.compliance_blocked} ${__("compliance blocked")} · ${
										row.unavailable
									} ${__("unavailable")}</small></button>`
							)
							.join("")
					: __("No transport-enabled fleet is configured for this company.")
			);
	}

	render_drivers(data) {
		this.driver_summary = data;
		this.$root
			.find(".dp-drivers")
			.removeClass("dp-empty")
			.html(
				`<button class="dp-availability-row dp-driver-details"><strong>${data.available}/${
					data.total
				} ${__("available")}</strong><span>${data.allocated} ${__("allocated")} · ${
					data.in_trip
				} ${__("in trip")}</span><small>${data.unavailable} ${__(
					"unavailable"
				)}</small></button>`
			);
	}

	async show_vehicle_details(event) {
		const category = $(event.currentTarget).data("category");
		const { company } = this.get_filter_values();
		const window = this.planning_window();
		if (!window) return;
		const rows = await this.call("get_vehicle_availability_details", {
			company,
			vehicle_category: category,
			...window,
		});
		this.show_availability_dialog(category, rows, "license_plate");
	}

	async show_driver_details() {
		const { company } = this.get_filter_values();
		const window = this.planning_window();
		if (!window) return;
		const rows = await this.call("get_driver_availability_details", { company, ...window });
		this.show_availability_dialog(__("Driver Availability"), rows, "driver_name");
	}

	show_availability_dialog(title, rows, label_field) {
		const dialog = new frappe.ui.Dialog({ title, size: "large" });
		dialog.$body.html(
			`<div class="dp-dialog-list">${rows
				.map((row) => {
					const reasons = (row.reasons || [])
						.map((reason) => `<li>${this.escape(reason.message)}</li>`)
						.join("");
					return `<div class="dp-dialog-row"><strong>${this.escape(
						row[label_field]
					)}</strong><span class="indicator-pill ${
						row.dispatch_eligible ? "green" : "red"
					}">${this.escape(row.operational_state)}</span><span>${this.escape(
						row.primary_reason || __("Available for this window")
					)}</span>${
						reasons.length > 0
							? `<details><summary>${__(
									"View all blockers"
							  )}</summary><ul>${reasons}</ul></details>`
							: ""
					}</div>`;
				})
				.join("")}</div>`
		);
		dialog.show();
	}

	clear_plan() {
		this.allocations.clear();
		this.details = {};
		this.plan = null;
		this.render_plan();
	}

	clear_suggestions() {
		this.suggestions = [];
		this.$root
			.find(".dp-suggestions")
			.addClass("dp-empty")
			.html(__("Run suggestions for the current filters or selected demand."));
	}

	order_selected(name) {
		return [...this.allocations.values()].some((row) => row.delivery_order === name);
	}

	requirements_text(requirements) {
		return (
			[
				requirements.refrigeration_required ? __("Refrigerated") : "",
				requirements.tail_lift_required ? __("Tail lift") : "",
				requirements.required_body_type || "",
				requirements.special_equipment_required || "",
			]
				.filter(Boolean)
				.join(" · ") || __("No special transport requirements")
		);
	}

	async call(method, args, freeze = false) {
		const response = await frappe.call({
			method: `vuna_transport.services.planning.dispatch_planning_service.${method}`,
			args,
			freeze,
			freeze_message: freeze ? __("Validating plan...") : undefined,
		});
		return response.message;
	}

	loading() {
		return `<div class="dp-empty">${__("Loading...")}</div>`;
	}
	error_message(error) {
		return `<div class="dp-empty text-danger">${this.escape(
			error?.message || __("Unable to load planning data.")
		)}</div>`;
	}
	number(value) {
		return format_number(flt(value), null, 2);
	}
	escape(value) {
		return frappe.utils.escape_html(String(value ?? ""));
	}
	selector(value) {
		return CSS.escape(String(value));
	}
};
