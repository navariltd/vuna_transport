frappe.provide("vuna_transport.DispatchPlanning");

frappe.pages["dispatch-planning"].on_page_load = function (wrapper) {
	$(wrapper).addClass("dispatch-planning-page");
	$(wrapper)
		.closest(".page-container")
		.addClass("dispatch-planning-page")
		.find("footer")
		.remove();
	frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Dispatch Planning"),
		single_column: true,
		hide_sidebar: true,
	});
	wrapper.page.set_secondary_action(
		__("Back to Transport Loads"),
		() => frappe.set_route("List", "Transport Load"),
		"arrow-left"
	);
	// This planning page has its own full-height workspace and does not need the
	// standard empty page footer reserved by make_app_page.
	$(wrapper).find(".layout-footer, .page-footer").remove();
	$(wrapper).closest(".page-container").find("footer").remove();
	load_vue(wrapper);
};

frappe.pages["dispatch-planning"].on_page_show = function (wrapper) {
	if (frappe.dispatch_planning_controller) {
		frappe.dispatch_planning_controller.refresh();
	} else if (!frappe.dispatch_planning_vue_loading) {
		load_vue(wrapper);
	}
};

async function load_vue(wrapper) {
	if (frappe.dispatch_planning_vue_loading) return frappe.dispatch_planning_vue_loading;
	frappe.dispatch_planning_vue_loading = (async () => {
		const $parent = $(wrapper).find(".layout-main-section");
		$parent.empty();
		await frappe.require("dispatch_planning.bundle.js");
		frappe.dispatch_planning_vue_app?.unmount();
		frappe.dispatch_planning_vue_app = frappe.ui.setup_dispatch_planning_vue($parent);
	})();
	try {
		return await frappe.dispatch_planning_vue_loading;
	} finally {
		frappe.dispatch_planning_vue_loading = null;
	}
}

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
		this.plan_evaluation_timer = null;
		this.plan_evaluation_id = 0;
		this.refresh_request_id = 0;
		this.availability_request_id = 0;
		this.suggestion_request_id = 0;
		this.cost_request_id = 0;
		this.details_requests = new Map();
		this.configured_filter_keys = new Set();
		this.configured_filter_company = null;
		this.make_layout();
		this.make_filters();
		this.make_native_filter_group();
		this.bind_events();
		this.load_configured_filters();
		this.load_vehicle_categories();
	}

	destroy() {
		this.destroyed = true;
		clearTimeout(this.filter_timer);
		clearTimeout(this.plan_evaluation_timer);
		this.$root?.off();
	}

	make_layout() {
		this.$root = $(
			`<div class="dispatch-planning">
				<div class="dp-toolbar">
					<div class="dp-toolbar-header dp-collapsible-header" role="button" tabindex="0" aria-expanded="false">
						<div class="dp-filter-title">${__(
							"Planning Filters"
						)} <span class="text-muted dp-filter-toggle-icon">▸</span></div>
						<div class="dp-toolbar-actions">
							<button class="btn btn-default btn-sm btn-toggle-filters">${__("Show Filters")}</button>
							<button class="btn btn-default btn-sm btn-native-filter">${__("Add Filter")}</button>
							<button class="btn btn-default btn-sm btn-clear-native-filter" title="${__(
								"Clear added filters"
							)}">×</button>
						<button class="btn btn-default btn-refresh">${__("Refresh")}</button>
						<button class="btn btn-default btn-suggest">${__("Suggest Loads")}</button>
						</div>
					</div>
					<div class="dp-filter-grid dp-filter-grid-collapsed"></div>
				</div>
				<div class="dp-grid">
					<div class="dp-main">
						<div class="dp-section dp-demand-section">
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
							<div class="dp-section-title">${__("Loading Plan")}</div>
							<div class="dp-category-picker"><label>${__(
								"Vehicle Category"
							)} <span class="text-muted">→</span></label><select class="form-control dp-category-select"><option value="">${__(
				"Select"
			)}</option></select></div>
							<div class="dp-plan-content dp-empty">${__("Select Delivery Orders to begin planning.")}</div>
							<div class="dp-costed-vehicles"></div>
							<div class="dp-plan-actions">
								<button class="btn btn-default btn-sm btn-evaluate" disabled>${__("Evaluate")}</button>
								<button class="btn btn-primary btn-sm btn-create-load" disabled>${__(
									"Create Transport Load"
								)}</button>
							</div>
						</div>
						<div class="dp-section dp-fleet-section">
							<div class="dp-section-title">${__(
								"Fleet Availability"
							)} <small class="text-muted dp-availability-date"></small></div>
							<div class="dp-fleet dp-empty">${__("Choose a company.")}</div>
						</div>
						<div class="dp-section dp-driver-section">
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
			["territory", "Link", __("Territory"), "Territory"],
			["county", "Link", __("County"), "County"],
			["town", "Link", __("Town"), "Town"],
			["customer", "Link", __("Customer"), "Customer"],
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
		this.filters.territory.get_query = () => ({ filters: { is_group: 0 } });
	}

	async make_native_filter_group() {
		const $button = this.$root.find(".btn-native-filter");
		$button.prop("disabled", true);
		try {
			await frappe.model.with_doctype("Delivery Order");
		} catch (error) {
			$button.prop("disabled", false);
			frappe.show_alert({
				message: error?.message || __("Unable to load Delivery Order fields."),
				indicator: "red",
			});
			return;
		}
		if (this.destroyed) return;
		this.filter_group = new frappe.ui.FilterGroup({
			parent: this.$root.find(".dp-toolbar-actions"),
			doctype: "Delivery Order",
			filter_button: this.$root.find(".btn-native-filter"),
			filter_x_button: this.$root.find(".btn-clear-native-filter"),
			on_change: () => this.filters_changed(),
		});
		$button.prop("disabled", false);
	}

	async load_configured_filters() {
		if (this.configured_filter_company === "global") return;
		try {
			for (const key of this.configured_filter_keys) {
				this.filters[key]?.$wrapper?.remove();
				delete this.filters[key];
			}
			this.configured_filter_keys.clear();
			this.configured_filter_company = "global";
			const response = await frappe.call({
				method: "vuna_transport.services.planning.dispatch_planning_service.get_dispatch_planning_filter_definitions",
				args: {},
			});
			for (const definition of response.message || []) {
				if (this.filters[definition.field_key]) continue;
				const field = frappe.ui.form.make_control({
					parent: this.$root.find(".dp-filter-grid"),
					df: {
						fieldname: definition.field_key,
						fieldtype: definition.fieldtype,
						label: definition.label,
						options: definition.options,
						onchange: () => this.filters_changed(),
					},
					render_input: true,
				});
				field.set_value("");
				this.filters[definition.field_key] = field;
				this.configured_filter_keys.add(definition.field_key);
			}
			this.configure_location_filter_queries();
		} catch (error) {
			this.configured_filter_company = null;
			frappe.show_alert({
				message: error?.message || __("Unable to load configured planning filters."),
				indicator: "red",
			});
		}
	}

	configure_location_filter_queries() {
		if (this.filters.territory)
			this.filters.territory.get_query = () => ({ filters: { is_group: 0 } });
		if (this.filters.county)
			this.filters.county.get_query = () => ({
				filters: this.filters.territory?.get_value()
					? { territory: this.filters.territory.get_value(), disabled: 0 }
					: { disabled: 0 },
			});
		if (this.filters.town)
			this.filters.town.get_query = () => ({
				filters: this.filters.constituency?.get_value()
					? { constituency: this.filters.constituency.get_value(), disabled: 0 }
					: this.filters.county?.get_value()
					? { county: this.filters.county.get_value(), disabled: 0 }
					: { disabled: 0 },
			});
		if (this.filters.constituency)
			this.filters.constituency.get_query = () => ({
				filters: this.filters.county?.get_value()
					? { county: this.filters.county.get_value(), disabled: 0 }
					: { disabled: 0 },
			});
	}

	async load_vehicle_categories() {
		try {
			const response = await frappe.db.get_list("Vehicle Category", {
				filters: { disabled: 0 },
				fields: ["name", "vehicle_category_name"],
				order_by: "vehicle_category_name asc",
				limit: 100,
			});
			const options = response
				.map(
					(category) =>
						`<option value="${this.escape(category.name)}">${this.escape(
							category.vehicle_category_name || category.name
						)}</option>`
				)
				.join("");
			this.$root.find(".dp-category-select").append(options);
		} catch (error) {
			this.$root.find(".dp-category-select").prop("disabled", true);
			frappe.show_alert({
				message: error?.message || __("Unable to load Vehicle Categories."),
				indicator: "red",
			});
		}
	}

	bind_events() {
		this.$root.on("click keydown", ".dp-collapsible-header", (event) => {
			if (event.type === "keydown" && event.key !== "Enter" && event.key !== " ") return;
			if (event.target.closest("button")) return;
			event.preventDefault();
			this.toggle_filters();
		});
		this.$root.on("click", ".btn-toggle-filters", (event) => {
			event.stopPropagation();
			this.toggle_filters();
		});
		this.$root.on("click", ".btn-refresh", () => this.refresh(true));
		this.$root.on("click", ".btn-suggest", () => this.load_suggestions());
		this.$root.on("click", ".btn-evaluate", () => this.evaluate_plan());
		this.$root.on("click", ".btn-create-load", () => this.create_load());
		this.$root.on("click", ".btn-prev", () => this.change_page(-1));
		this.$root.on("click", ".btn-next", () => this.change_page(1));
		this.$root.on("change", ".dp-order-select", (event) => this.toggle_order(event));
		this.$root.on("change", ".dp-select-all-orders", (event) => this.toggle_all_orders(event));
		this.$root.on("click", ".dp-expand-order", (event) => this.toggle_details(event));
		this.$root.on("change", ".dp-allocation-qty", (event) => this.change_quantity(event));
		this.$root.on("change", ".dp-category-select", () => this.category_changed());
		this.$root.on("click", ".dp-use-suggestion", (event) => this.use_suggestion(event));
		this.$root.on("click", ".dp-create-suggestion", (event) =>
			this.create_from_suggestion(event)
		);
		this.$root.on("click", ".dp-fleet-category", (event) => this.show_vehicle_details(event));
		this.$root.on("click", ".dp-driver-details", () => this.show_driver_details());
	}

	category_changed() {
		this.clear_suggestions();
		this.plan = null;
		this.plan_evaluation_id += 1;
		this.render_plan();
		this.refresh(false, false);
		this.schedule_plan_evaluation();
	}

	toggle_filters() {
		const $filters = this.$root.find(".dp-filter-grid");
		const collapsed = $filters
			.toggleClass("dp-filter-grid-collapsed")
			.hasClass("dp-filter-grid-collapsed");
		this.$root
			.find(".btn-toggle-filters")
			.text(collapsed ? __("Show Filters") : __("Hide Filters"));
		this.$root
			.find(".dp-collapsible-header")
			.attr("aria-expanded", collapsed ? "false" : "true");
		this.$root.find(".dp-filter-toggle-icon").text(collapsed ? "▸" : "▾");
	}

	filters_changed() {
		this.load_configured_filters();
		this.start = 0;
		this.clear_suggestions();
		clearTimeout(this.filter_timer);
		this.filter_timer = setTimeout(() => this.refresh(true), 300);
	}

	get_filter_values() {
		return Object.fromEntries(
			Object.entries(this.filters).map(([key, field]) => [
				key,
				key.startsWith("required_delivery_date_")
					? this.valid_date_filter(field.get_value())
					: field.get_value(),
			])
		);
	}

	get_configured_filter_values(filters) {
		return Object.fromEntries(
			[...this.configured_filter_keys]
				.filter((key) => filters[key])
				.map((key) => [key, filters[key]])
		);
	}

	get_native_filter_values() {
		return this.filter_group?.get_filters?.() || [];
	}

	planning_window() {
		const filter_from = this.filters.required_delivery_date_from.get_value();
		const filter_to = this.filters.required_delivery_date_to.get_value();
		const date_from = this.valid_date_filter(filter_from);
		const date_to = this.valid_date_filter(filter_to);
		const fallback = frappe.datetime.get_today();
		const start_date = date_from || date_to || fallback;
		const end_date = date_to || date_from || fallback;
		if (start_date > end_date) return null;
		return {
			from_time: `${start_date} 00:00:00`,
			to_time: `${frappe.datetime.add_days(end_date, 1)} 00:00:00`,
		};
	}

	valid_date_filter(value) {
		if (!value) return null;
		const normalized = String(value).trim().toLowerCase();
		if (normalized.includes("undefined") || normalized.includes("null")) return null;
		return value;
	}

	async refresh(clear_selection = false, refresh_resources = true) {
		const filters = this.get_filter_values();
		if (!filters.company) return;
		const request_id = ++this.refresh_request_id;
		this.clear_suggestions();
		if (clear_selection) this.clear_plan();
		this.$root.find(".dp-demand").html(this.loading());
		const args = {
			...filters,
			planning_filters: this.get_configured_filter_values(filters),
			additional_filters: this.get_native_filter_values(),
			required_vehicle_category: this.selected_vehicle_category(),
			selected_delivery_order_names: [
				...new Set([...this.allocations.values()].map((row) => row.delivery_order)),
			],
			start: this.start,
			page_length: this.page_length,
		};
		if (refresh_resources) {
			const availability_request_id = ++this.availability_request_id;
			this.refresh_availability(availability_request_id);
		}
		try {
			const demand = await this.call("get_planning_demand", args);
			if (request_id !== this.refresh_request_id) return;
			this.orders = demand.orders || [];
			this.has_more = demand.has_more;
			this.render_demand();
		} catch (error) {
			if (request_id !== this.refresh_request_id) return;
			this.$root.find(".dp-demand").html(this.error_message(error));
		}
	}

	selected_vehicle_category() {
		return this.$root.find(".dp-category-select").val() || null;
	}

	async refresh_availability(request_id = this.availability_request_id) {
		const { company } = this.get_filter_values();
		if (!company) return;
		this.$root.find(".dp-fleet, .dp-drivers").html(this.loading());
		const window = this.planning_window();
		if (!window) {
			const message = __("Delivery Date From cannot be after Delivery Date To.");
			this.$root.find(".dp-fleet, .dp-drivers").addClass("dp-empty").html(message);
			return;
		}
		const filter_from = this.filters.required_delivery_date_from.get_value();
		const filter_to = this.filters.required_delivery_date_to.get_value();
		const date_from = this.valid_date_filter(filter_from);
		const date_to = this.valid_date_filter(filter_to);
		const fallback = frappe.datetime.get_today();
		const start_date = date_from || date_to || fallback;
		const end_date = date_to || date_from || fallback;
		const availability_label =
			!date_from && !date_to
				? `${__("Today")}, ${frappe.datetime.str_to_user(start_date)}`
				: start_date === end_date
				? frappe.datetime.str_to_user(start_date)
				: `${frappe.datetime.str_to_user(start_date)} – ${frappe.datetime.str_to_user(
						end_date
				  )}`;
		this.$root.find(".dp-availability-date").text(`— ${availability_label}`);
		const requests = [
			[".dp-fleet", "get_fleet_availability_summary", (data) => this.render_fleet(data)],
			[
				".dp-drivers",
				"get_driver_availability_summary",
				(data) => this.render_drivers(data),
			],
		];
		await Promise.all(
			requests.map(async ([selector, method, render]) => {
				try {
					const data = await this.call(method, { company, ...window });
					if (request_id === this.availability_request_id) render(data);
				} catch (error) {
					if (request_id === this.availability_request_id)
						this.$root.find(selector).html(this.error_message(error));
				}
			})
		);
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
				return `<div class="dp-demand-row${
					order.vehicle_category_incompatible ? " dp-demand-row-warning" : ""
				}" data-order="${this.escape(order.name)}" title="${this.escape(
					order.vehicle_category_incompatible
						? __("Selected order is not compatible with this Vehicle Category.")
						: ""
				)}">
				<div><input type="checkbox" class="dp-order-select" title="${__(
					"Load all remaining items from this Delivery Order"
				)}" aria-label="${__("Load all remaining items")}" ${selected}></div>
				<div><a href="/app/delivery-order/${encodeURIComponent(order.name)}"><strong>${this.escape(
					order.name
				)}</strong></a><small>${this.escape(
					order.customer_name || order.customer
				)}</small></div>
				<div><span>${this.escape(order.source_warehouse)}</span><small>${this.escape(
					order.territory || __("No territory")
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
				`<div class="dp-demand-header"><span><input type="checkbox" class="dp-select-all-orders" title="${__(
					"Select all visible Delivery Orders"
				)}" aria-label="${__("Select all visible Delivery Orders")}"></span><span>${__(
					"Order / Customer"
				)}</span><span>${__("Warehouse / Territory")}</span><span>${__(
					"Date / Priority"
				)}</span><span>${__("Weight / Volume")}</span><span>${__(
					"Allocated / Requested"
				)}</span><span>${__("Requirements")}</span><span></span></div>${rows}`
			);
		this.update_select_all_state();
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
		this.refresh(false, false);
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
		this.plan_evaluation_id += 1;
		this.plan = null;
		this.clear_suggestions();
		this.render_plan();
		this.schedule_plan_evaluation();
		this.update_select_all_state();
	}

	async toggle_all_orders(event) {
		const names = this.orders.map((order) => order.name);
		if (!names.length) return;
		if (event.currentTarget.checked) {
			await this.ensure_details(names);
			for (const name of names) {
				for (const item of this.details[name]?.items || []) {
					if (item.available_quantity > 0) {
						this.allocations.set(item.name, {
							delivery_order: name,
							delivery_order_item: item.name,
							allocated_quantity: item.available_quantity,
						});
					}
				}
			}
		} else {
			for (const [key, row] of this.allocations) {
				if (names.includes(row.delivery_order)) this.allocations.delete(key);
			}
		}
		this.plan_evaluation_id += 1;
		this.plan = null;
		this.clear_suggestions();
		this.render_demand();
		this.render_plan();
		this.schedule_plan_evaluation();
	}

	update_select_all_state() {
		const total = this.orders.length;
		const selected = this.orders.filter((order) => this.order_selected(order.name)).length;
		const checkbox = this.$root.find(".dp-select-all-orders").get(0);
		if (!checkbox) return;
		checkbox.checked = total > 0 && selected === total;
		checkbox.indeterminate = selected > 0 && selected < total;
	}

	async toggle_details(event) {
		const name = $(event.currentTarget).closest(".dp-demand-row").data("order");
		const $target = this.$root.find(`[data-details="${this.selector(name)}"]`);
		if ($target.children().length) return $target.toggle();
		$target.addClass("dp-details-loading").html(this.loading()).show();
		try {
			await this.ensure_details([name]);
			$target
				.removeClass("dp-details-loading")
				.html(this.render_item_table(this.details[name]));
		} catch (error) {
			$target.removeClass("dp-details-loading").html(this.error_message(error));
		}
	}

	async ensure_details(names) {
		const missing = names.filter((name) => !this.details[name]);
		if (!missing.length) return;
		const request_key = [...missing].sort().join("\u0000");
		if (!this.details_requests.has(request_key)) {
			this.details_requests.set(
				request_key,
				this.call("get_delivery_order_details", {
					delivery_order_names: missing,
				})
					.then((rows) => {
						for (const row of rows) this.details[row.name] = row;
						return rows;
					})
					.finally(() => this.details_requests.delete(request_key))
			);
		}
		await this.details_requests.get(request_key);
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
				const stock_sufficient = item.stock_availability_status === "Available";
				return `<div class="dp-item-row"><span><strong>${this.escape(
					item.item_code
				)}</strong><small>${this.escape(
					item.item_name || ""
				)}</small></span><span>${this.number(item.transport_quantity)} ${this.escape(
					item.stock_uom || ""
				)}<small>${this.number(item.allocated_quantity)} ${__(
					"allocated"
				)}</small></span><span>${this.number(item.available_quantity)} ${__(
					"remaining"
				)}<small class="${
					stock_sufficient ? "text-success" : "text-warning"
				}">${this.number(item.stock_available_quantity)} ${this.escape(
					item.stock_uom || ""
				)} ${
					stock_sufficient ? __("on hand") : __("on hand — insufficient")
				}</small></span><span>${this.number(item.weight)} / ${this.number(
					item.volume
				)}<small>${this.escape(
					item.handling_requirements || __("No handling profile")
				)}</small></span><span class="dp-qty-entry"><label>${__(
					"Load Qty"
				)}</label><input type="number" class="form-control dp-allocation-qty" placeholder="${__(
					"Enter quantity"
				)}" title="${__(
					"Enter the quantity to include in this Transport Load"
				)}" data-order="${this.escape(order.name)}" data-item="${this.escape(
					item.name
				)}" min="0" max="${item.available_quantity}" step="any" value="${
					allocation?.allocated_quantity || 0
				}"></span></div>`;
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
		this.plan_evaluation_id += 1;
		this.plan = null;
		this.clear_suggestions();
		this.render_plan();
		this.schedule_plan_evaluation();
	}

	render_plan() {
		const rows = [...this.allocations.values()];
		const $content = this.$root.find(".dp-plan-content");
		const selected_category = this.selected_vehicle_category();
		this.$root.find(".btn-evaluate").prop("disabled", !rows.length);
		this.$root.find(".btn-create-load").prop("disabled", !this.plan);
		if (!rows.length)
			return this.$root
				.find(".dp-costed-vehicles")
				.empty()
				.end()
				.find(".dp-plan-content")
				.addClass("dp-empty")
				.html(__("Select Delivery Orders to begin planning."));
		if (!this.plan)
			return this.$root
				.find(".dp-costed-vehicles")
				.empty()
				.end()
				.find(".dp-plan-content")
				.removeClass("dp-empty")
				.html(
					`<strong>${__("{0} item allocations selected", [
						rows.length,
					])}</strong><p class="text-muted">${__(
						"Capacity and compatible vehicle categories are calculated automatically."
					)}</p>`
				);
		const totals = this.plan.totals;
		const selected_category_row = this.plan.compatible_vehicle_categories.find(
			(category) => category.name === selected_category
		);
		const recommended_category_row = this.plan.compatible_vehicle_categories.find(
			(category) => category.name === this.plan.recommended_vehicle_category
		);
		const category_row = selected_category_row || recommended_category_row;
		const capacity = category_row?.capacity;
		const category_label =
			category_row?.label || selected_category || this.plan.recommended_vehicle_category;
		const category_caption = selected_category
			? __("Selected Vehicle Category: {0}")
			: __("Recommended Vehicle Category: {0}");
		$content
			.removeClass("dp-empty")
			.html(
				`<div class="dp-plan-metrics"><span><strong>${this.plan.total_stops}</strong>${__(
					"Stops"
				)}</span><span><strong>${this.number(totals.weight)}</strong>${this.escape(
					this.plan.weight_uom || __("Weight")
				)}</span><span><strong>${this.number(totals.volume)}</strong>${this.escape(
					this.plan.volume_uom || __("Volume")
				)}</span></div>${this.render_capacity_breakdown(
					capacity
				)}<div class="small text-muted dp-selected-category">${category_caption.replace(
					"{0}",
					this.escape(category_label || __("None"))
				)}</div><div class="small text-muted dp-requirements">${this.escape(
					this.requirements_text(this.plan.requirements)
				)}</div>`
			);
		const can_create = selected_category
			? this.plan.selected_vehicle_category_compatible
			: Boolean(this.plan.recommended_vehicle_category);
		this.$root.find(".btn-create-load").prop("disabled", !can_create);
		this.load_cost_structure_estimates();
	}

	async load_cost_structure_estimates() {
		const request_id = ++this.cost_request_id;
		const rows = [...this.allocations.values()];
		const $target = this.$root.find(".dp-costed-vehicles");
		if (!rows.length || !this.plan) return $target.empty();
		$target.html(
			`<div class="text-muted small">${__("Loading cost structure estimates...")}</div>`
		);
		try {
			const response = await this.call("get_cost_structure_estimates", {
				allocations: rows,
				vehicle_category:
					this.selected_vehicle_category() || this.plan.recommended_vehicle_category,
			});
			if (request_id !== this.cost_request_id) return;
			const candidates = response?.cost_structures || [];
			$target.html(
				candidates.length
					? `<div class="small text-muted">${__(
							"Applicable Cost Structures ranked by estimated cost"
					  )}</div>${candidates
							.map(
								(row, index) =>
									`<div class="dp-costed-vehicle"><strong>${
										index + 1
									}. ${this.escape(
										row.cost_structure_label || row.cost_structure
									)}</strong><span>${
										row.cost_structure_assignment ||
										__("No applicable assignment")
									}</span><b>${
										row.currency
											? `${this.number(
													row.estimated_full_cost
											  )} ${this.escape(row.currency)}`
											: __("Not estimated")
									}</b>${
										row.recommended
											? `<small class="text-success">${__(
													"Recommended"
											  )}</small>`
											: ""
									}${
										row.warnings?.length
											? `<small class="text-warning">${this.escape(
													row.warnings[0]
											  )}</small>`
											: ""
									}</div>`
							)
							.join("")}`
					: `<div class="text-muted small">${__(
							"No applicable Cost Structure estimates found."
					  )}</div>`
			);
		} catch (error) {
			if (request_id === this.cost_request_id)
				$target.html(
					`<div class="text-muted small">${this.escape(
						error.message || __("Cost Structure estimates unavailable.")
					)}</div>`
				);
		}
	}

	render_capacity_breakdown(capacity) {
		if (!capacity) return "";
		const labels = {
			weight: __("Weight"),
			volume: __("Volume"),
		};
		const capacity_rows = Object.entries(capacity)
			.filter(([dimension]) => ["weight", "volume"].includes(dimension))
			.filter(([, values]) => values.capacity);
		const has_full_dimension = capacity_rows.some(([, values]) => values.remaining <= 0);
		const has_healthy_dimension = capacity_rows.some(([, values]) => values.utilization >= 70);
		const overall_status = has_full_dimension
			? "full"
			: has_healthy_dimension
			? "healthy"
			: "available";
		const overall_label = {
			available: __("Capacity Available"),
			healthy: __("Capacity Available"),
			full: __("Capacity Full"),
			unknown: __("Capacity Not Configured"),
		}[overall_status];
		const rows = capacity_rows
			.map(([dimension, values]) => {
				const row_status =
					values.remaining <= 0
						? "full"
						: values.utilization >= 70
						? "healthy"
						: "available";
				const progress = Math.min(100, Math.max(0, Number(values.utilization) || 0));
				return `<div class="dp-capacity-row"><span>${
					labels[dimension]
				}</span><strong>${this.number(values.used)} / ${this.number(
					values.capacity
				)}</strong><small class="dp-capacity-value ${row_status}">${this.number(
					values.remaining
				)} ${__("available")} · ${this.number(
					values.utilization
				)}%</small><div class="dp-capacity-progress" role="progressbar" aria-valuenow="${progress}" aria-valuemin="0" aria-valuemax="100"><span class="${row_status}" style="width: ${progress}%"></span></div></div>`;
			})
			.join("");
		return rows
			? `<div class="dp-capacity-breakdown"><div class="dp-capacity-status dp-capacity-${overall_status}"><strong>${overall_label}</strong></div>${rows}</div>`
			: "";
	}

	async evaluate_plan() {
		clearTimeout(this.plan_evaluation_timer);
		const evaluation_id = ++this.plan_evaluation_id;
		const rows = [...this.allocations.values()];
		if (!rows.length) return;
		this.$root.find(".dp-plan-content").html(this.loading());
		try {
			const plan = await this.call("evaluate_manual_load_plan", {
				allocations: rows,
				vehicle_category: this.$root.find(".dp-category-select").val() || null,
			});
			if (evaluation_id !== this.plan_evaluation_id) return;
			this.plan = plan;
			this.render_plan();
		} catch (error) {
			if (evaluation_id !== this.plan_evaluation_id) return;
			this.plan = null;
			this.$root.find(".dp-plan-content").html(this.error_message(error));
		}
	}

	schedule_plan_evaluation() {
		clearTimeout(this.plan_evaluation_timer);
		if (!this.allocations.size) return;
		this.$root.find(".dp-plan-content").html(this.loading());
		this.plan_evaluation_timer = setTimeout(() => this.evaluate_plan(), 300);
	}

	async create_load() {
		if (!this.plan) return;
		try {
			const vehicle_category =
				this.$root.find(".dp-category-select").val() ||
				this.plan.recommended_vehicle_category;
			const name = await this.call(
				"create_delivery_load_from_plan",
				{ allocations: [...this.allocations.values()], vehicle_category },
				true
			);
			frappe.show_alert({
				message: __("Transport Load {0} created", [name]),
				indicator: "green",
			});
			frappe.set_route("Form", "Transport Load", name);
		} catch (error) {
			frappe.msgprint(this.error_message(error));
		}
	}

	async load_suggestions() {
		const filters = this.get_filter_values();
		if (!filters.company) return;
		const request_id = ++this.suggestion_request_id;
		this.$root.find(".btn-suggest").prop("disabled", true);
		this.$root.find(".dp-suggestions").html(this.loading());
		const selected_names = [
			...new Set([...this.allocations.values()].map((row) => row.delivery_order)),
		];
		try {
			const response = await frappe.call({
				method: "vuna_transport.services.planning.load_suggestion_service.suggest_delivery_loads",
				args: {
					company: filters.company,
					source_warehouse: filters.source_warehouse || null,
					transport_zone: filters.transport_zone || null,
					delivery_method: filters.delivery_method || null,
					priority: filters.priority || null,
					customer: filters.customer || null,
					delivery_address: filters.delivery_address || null,
					territory: filters.territory || null,
					county: filters.county || null,
					constituency: filters.constituency || null,
					town: filters.town || null,
					planning_filters: this.get_configured_filter_values(filters),
					additional_filters: this.get_native_filter_values(),
					delivery_date_from: filters.required_delivery_date_from || null,
					delivery_date_to: filters.required_delivery_date_to || null,
					delivery_order_names: selected_names.length ? selected_names : null,
				},
				freeze: true,
				freeze_message: __("Building load suggestions..."),
			});
			if (request_id !== this.suggestion_request_id) return;
			const result = response.message || {};
			this.suggestions = result.suggestions || [];
			this.render_suggestions(result);
		} catch (error) {
			if (request_id !== this.suggestion_request_id) return;
			this.suggestions = [];
			this.$root.find(".dp-suggestions").html(this.error_message(error));
		} finally {
			if (request_id === this.suggestion_request_id)
				this.$root.find(".btn-suggest").prop("disabled", false);
		}
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
		)}</strong><span class="indicator-pill ${
			suggestion.recommended ? "green" : "blue"
		}">${this.number(suggestion.score)}%</span></div>
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
		try {
			const response = await frappe.call({
				method: "vuna_transport.services.planning.load_suggestion_service.create_delivery_load_from_suggestion",
				args: { suggestion },
				freeze: true,
				freeze_message: __("Revalidating and creating Transport Load..."),
			});
			frappe.set_route("Form", "Transport Load", response.message);
		} catch (error) {
			frappe.msgprint(this.error_message(error));
		}
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
		clearTimeout(this.plan_evaluation_timer);
		this.plan_evaluation_id += 1;
		this.allocations.clear();
		this.details = {};
		this.plan = null;
		this.render_plan();
	}

	clear_suggestions() {
		this.suggestion_request_id += 1;
		this.suggestions = [];
		this.$root.find(".btn-suggest").prop("disabled", false);
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
		return `<div class="dp-loading"><span class="dp-spinner" aria-hidden="true"></span><span>${__(
			"Loading..."
		)}</span></div>`;
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
