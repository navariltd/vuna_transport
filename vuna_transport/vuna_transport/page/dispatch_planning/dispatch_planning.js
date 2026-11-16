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
	planning_state(tab = this.active_planning_tab || "customers") {
		this.planning_states ||= {};
		return (this.planning_states[tab] ||= {
			orders: [],
			details: {},
			allocations: new Map(),
			details_requests: new Map(),
			has_more: false,
		});
	}

	get orders() {
		return this.planning_state().orders;
	}
	set orders(value) {
		this.planning_state().orders = value;
	}
	get details() {
		return this.planning_state().details;
	}
	set details(value) {
		this.planning_state().details = value;
	}
	get allocations() {
		return this.planning_state().allocations;
	}
	set allocations(value) {
		this.planning_state().allocations = value;
	}
	get details_requests() {
		return this.planning_state().details_requests;
	}
	set details_requests(value) {
		this.planning_state().details_requests = value;
	}
	get has_more() {
		return this.planning_state().has_more;
	}
	set has_more(value) {
		this.planning_state().has_more = value;
	}

	demand_target() {
		return (
			{ depots: ".dp-transfer-demand", suppliers: ".dp-supplier-demand" }[
				this.active_planning_tab
			] || ".dp-demand"
		);
	}

	constructor(wrapper) {
		this.wrapper = wrapper;
		this.page = wrapper.page;
		this.customer_context = wrapper.customer_context || {
			doctype: "Transport Demand",
			order_field: "transport_demand",
			item_field: "transport_demand_item",
		};
		this.start = 0;
		this.transfer_start = 0;
		this.transfer_items = [];
		this.supplier_start = 0;
		this.supplier_items = [];
		this.page_length = 30;
		this.orders = [];
		this.details = {};
		this.collapsed_orders = new Set();
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
		this.make_vehicle_category_field();
		this.make_native_filter_group();
		this.bind_events();
		this.load_configured_filters();
	}

	destroy() {
		this.destroyed = true;
		this.close_grouping_popover();
		clearTimeout(this.filter_timer);
		clearTimeout(this.plan_evaluation_timer);
		this.$root?.off();
	}

	make_layout() {
		this.$root = $(
			`<div class="dispatch-planning">
				<div class="dp-toolbar">
					<div class="dp-toolbar-header">
						<div class="dp-toolbar-actions">
							<button class="btn btn-default btn-sm btn-native-filter">${__("Add Filter")}</button>
							<button class="btn btn-default btn-sm btn-clear-native-filter" title="${__(
								"Clear added filters"
							)}">×</button>
						<button class="btn btn-default btn-refresh">${__("Refresh")}</button>
						<button class="btn btn-default btn-suggest">${__("Suggest Loads")}</button>
						</div>
					</div>
					<div class="dp-filter-grid" role="group" aria-label="${__("Planning Filters")}"></div>
				</div>
				<div class="dp-planning-tabs" role="tablist" aria-label="${__("Planning demand type")}">
					<button type="button" class="dp-planning-tab active" id="dp-customer-tab" role="tab" aria-selected="true" aria-controls="dp-customer-panel" tabindex="0" data-planning-tab="customers">${__(
						"Orders"
					)}</button>
					<button type="button" class="dp-planning-tab" id="dp-depot-tab" role="tab" aria-selected="false" aria-controls="dp-depot-panel" tabindex="-1" data-planning-tab="depots">${__(
						"Depots"
					)}</button>
					<button type="button" class="dp-planning-tab" id="dp-supplier-tab" role="tab" aria-selected="false" aria-controls="dp-supplier-panel" tabindex="-1" data-planning-tab="suppliers">${__(
						"Suppliers"
					)}</button>
				</div>
				<div class="dp-grid">
					<div class="dp-main">
						<div class="dp-tab-panel" id="dp-supplier-panel" role="tabpanel" aria-labelledby="dp-supplier-tab" hidden>
							<div class="dp-section dp-demand-section">
								<div class="dp-section-heading"><div class="dp-section-title">${__(
									"Supplier Collections"
								)}</div></div>
								<div class="dp-supplier-demand"></div>
								<div class="dp-supplier-pagination" hidden><button class="btn btn-default btn-sm btn-supplier-prev">${__(
									"Previous"
								)}</button><button class="btn btn-default btn-sm btn-supplier-next">${__(
				"Next"
			)}</button></div>
							</div>
						</div>
						<div class="dp-tab-panel" id="dp-depot-panel" role="tabpanel" aria-labelledby="dp-depot-tab" hidden>
						<div class="dp-section dp-demand-section">
							<div class="dp-section-heading"><div class="dp-section-title">${__("Depot Transfers")}</div></div>
							<div class="dp-transfer-demand"></div>
							<div class="dp-transfer-pagination" hidden>
							<button class="btn btn-default btn-sm btn-transfer-prev">${__("Previous")}</button>
							<button class="btn btn-default btn-sm btn-transfer-next">${__("Next")}</button>
							</div>
						</div>
						</div>
						<div class="dp-tab-panel" id="dp-customer-panel" role="tabpanel" aria-labelledby="dp-customer-tab">
						<div class="dp-section dp-demand-section">
							<div class="dp-section-heading">
								<div class="dp-section-title">${__("Delivery Demand")}</div>
								<div class="dp-pagination"></div>
							</div>
							<div class="dp-demand"></div>
						</div>
						<details class="dp-section dp-suggestions-section">
							<summary class="dp-section-title">${__("Load Suggestions")}</summary>
							<div class="dp-suggestions dp-empty">${__(
								"Run suggestions for the current filters or selected demand."
							)}</div>
						</details>
						</div>
					</div>
					<div class="dp-sidebar">
						<div class="dp-section dp-plan">
							<div class="dp-section-title">${__("Loading Plan")}</div>
							<div class="dp-category-picker"></div>
							<div class="dp-plan-metrics"></div>
							<div class="dp-plan-actions">
								<button class="btn btn-default btn-sm btn-evaluate" disabled>${__("Evaluate")}</button>
								<button class="btn btn-primary btn-sm btn-create-load" disabled>${__(
									"Create Transport Load"
								)}</button>
							</div>
							<div class="dp-plan-content dp-empty">${__("Select demand items to begin planning.")}</div>
							<div class="dp-costed-vehicles"></div>
						</div>
						<details class="dp-section dp-fleet-section">
							<summary class="dp-section-title">${__(
								"Fleet Availability"
							)}</summary><small class="text-muted dp-availability-date"></small>
							<div class="dp-fleet dp-empty">${__("Choose a company.")}</div>
						</details>
						<details class="dp-section dp-driver-section">
							<summary class="dp-section-title">${__("Driver Availability")}</summary>
							<div class="dp-drivers dp-empty">${__("Choose a company.")}</div>
						</details>
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
			const field = this.make_planning_filter({
				fieldname,
				fieldtype,
				label,
				options,
				default: default_value,
				onchange: () => this.filters_changed(),
			});
			field.set_value(default_value || "");
			this.filters[fieldname] = field;
		}
		this.filters.territory.get_query = () => ({ filters: { is_group: 0 } });
	}

	make_planning_filter(df) {
		const field = frappe.ui.form.make_control({
			parent: this.$root.find(".dp-filter-grid"),
			df: { ...df, placeholder: df.label, input_class: "input-xs" },
			only_input: df.fieldtype !== "Check",
			render_input: true,
		});
		field.$input?.attr({ placeholder: df.label, "aria-label": df.label, title: df.label });
		return field;
	}

	async make_native_filter_group() {
		const $button = this.$root.find(".btn-native-filter");
		$button.prop("disabled", true);
		try {
			await frappe.model.with_doctype(this.customer_context.doctype);
		} catch (error) {
			$button.prop("disabled", false);
			frappe.show_alert({
				message: error?.message || __("Unable to load customer demand fields."),
				indicator: "red",
			});
			return;
		}
		if (this.destroyed) return;
		this.filter_group = new frappe.ui.FilterGroup({
			parent: this.$root.find(".dp-toolbar-actions"),
			doctype: this.customer_context.doctype,
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
				const field = this.make_planning_filter({
					fieldname: definition.field_key,
					fieldtype: definition.fieldtype,
					label: definition.label,
					options: definition.options,
					onchange: () => this.filters_changed(),
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

	make_vehicle_category_field() {
		this.vehicle_category = frappe.ui.form.make_control({
			parent: this.$root.find(".dp-category-picker"),
			df: {
				fieldname: "vehicle_category",
				fieldtype: "Link",
				label: __("Vehicle Category"),
				options: "Vehicle Category",
				placeholder: __("Search Vehicle Categories"),
				only_select: 1,
				onchange: () => this.category_changed(),
			},
			render_input: true,
		});
		this.vehicle_category.get_query = () => ({ filters: { disabled: 0 } });
	}

	bind_events() {
		this.$root.on("click", ".dp-planning-tab", (event) =>
			this.switch_planning_tab(event.currentTarget.dataset.planningTab)
		);
		this.$root.on("keydown", ".dp-planning-tab", (event) => {
			if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
			event.preventDefault();
			const tabs = ["customers", "depots", "suppliers"];
			const index = tabs.indexOf(event.currentTarget.dataset.planningTab);
			const tab =
				event.key === "Home"
					? tabs[0]
					: event.key === "End"
					? tabs[2]
					: tabs[(index + (event.key === "ArrowRight" ? 1 : 2)) % tabs.length];
			this.switch_planning_tab(tab);
			this.$root.find(`[data-planning-tab="${tab}"]`).trigger("focus");
		});
		this.$root.on("click", ".btn-supplier-prev", () => {
			this.supplier_start = Math.max(0, this.supplier_start - 20);
			this.refresh_suppliers(this.refresh_request_id);
		});
		this.$root.on("click", ".btn-supplier-next", () => {
			this.supplier_start += 20;
			this.refresh_suppliers(this.refresh_request_id);
		});
		this.$root.on("click", ".btn-transfer-prev", () => {
			this.transfer_start = Math.max(0, this.transfer_start - 20);
			this.refresh_transfers(this.refresh_request_id);
		});
		this.$root.on("click", ".btn-transfer-next", () => {
			this.transfer_start += 20;
			this.refresh_transfers(this.refresh_request_id);
		});
		this.$root.on("click", ".btn-refresh", () => this.refresh(true));
		this.$root.on("click", ".btn-suggest", () => this.load_suggestions());
		this.$root.on("click", ".btn-evaluate", () => this.evaluate_plan());
		this.$root.on("click", ".btn-create-load", () => this.create_load());
		this.$root.on("click", ".btn-prev", () => this.change_page(-1));
		this.$root.on("click", ".btn-next", () => this.change_page(1));
		this.$root.on("change", ".dp-order-select", (event) => this.toggle_order(event));
		this.$root.on("click", ".dp-order-toggle", (event) => this.toggle_order_details(event));
		this.$root.on("click", ".dp-grouping-trigger", (event) =>
			this.show_grouping_popover(event)
		);
		this.$root.on("change", ".dp-select-all-orders", (event) => this.toggle_all_orders(event));
		this.$root.on("change", ".dp-item-select", (event) => this.toggle_item(event));
		this.$root.on("change", ".dp-allocation-qty", (event) => this.change_quantity(event));
		this.$root.on("click", ".dp-use-suggestion", (event) => this.use_suggestion(event));
		this.$root.on("click", ".dp-create-suggestion", (event) =>
			this.create_from_suggestion(event)
		);
		this.$root.on("click", ".dp-fleet-category", (event) => this.show_vehicle_details(event));
		this.$root.on("click", ".dp-driver-details", () => this.show_driver_details());
	}

	switch_planning_tab(tab) {
		this.close_grouping_popover();
		if (!["customers", "depots", "suppliers"].includes(tab)) return;
		this.active_planning_tab = tab;
		clearTimeout(this.plan_evaluation_timer);
		this.plan_evaluation_id += 1;
		this.cost_request_id += 1;
		this.plan = null;
		const customers = tab === "customers";
		this.$root.find("#dp-customer-panel").prop("hidden", !customers);
		this.$root.find("#dp-depot-panel").prop("hidden", tab !== "depots");
		this.$root.find("#dp-supplier-panel").prop("hidden", tab !== "suppliers");
		for (const name of ["customers", "depots", "suppliers"]) {
			this.$root
				.find(`[data-planning-tab="${name}"]`)
				.toggleClass("active", name === tab)
				.attr("aria-selected", String(name === tab))
				.attr("tabindex", name === tab ? "0" : "-1");
		}
		this.$root
			.find(".btn-suggest, .btn-native-filter, .btn-clear-native-filter")
			.toggle(customers);
		this.render_demand();
		this.render_plan();
		this.schedule_plan_evaluation();
	}

	category_changed() {
		this.clear_suggestions();
		this.plan = null;
		this.plan_evaluation_id += 1;
		this.render_plan();
		this.refresh(false, false);
		this.schedule_plan_evaluation();
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
		this.close_grouping_popover();
		const filters = this.get_filter_values();
		if (!filters.company) return;
		const request_id = ++this.refresh_request_id;
		clearTimeout(this.plan_evaluation_timer);
		this.plan_evaluation_id += 1;
		this.plan = null;
		this.render_plan();
		if (clear_selection) this.transfer_start = 0;
		if (clear_selection) this.supplier_start = 0;
		this.refresh_transfers(request_id);
		this.refresh_suppliers(request_id);
		this.clear_suggestions();
		if (clear_selection) {
			for (const state of Object.values(this.planning_states || {}))
				state.allocations.clear();
			this.clear_plan();
		}
		// Stock is a live warehouse snapshot, not a permanent demand detail cache.
		const state = this.planning_state("customers");
		state.details = {};
		state.details_requests = new Map();
		this.$root.find(".dp-demand").html(this.loading());
		const args = {
			...filters,
			planning_filters: this.get_configured_filter_values(filters),
			additional_filters: this.get_native_filter_values(),
			required_vehicle_category: this.selected_vehicle_category(),
			selected_delivery_order_names: [
				...new Set([...state.allocations.values()].map((row) => row.delivery_order)),
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
			state.orders = demand.orders || [];
			state.has_more = demand.has_more;
			await this.ensure_details(
				state.orders.map((order) => order.name),
				state
			);
			if (request_id !== this.refresh_request_id || this.destroyed) return;
			if (!this.active_planning_tab || this.active_planning_tab === "customers") {
				this.render_demand();
				this.schedule_plan_evaluation();
			}
		} catch (error) {
			if (request_id !== this.refresh_request_id) return;
			this.$root.find(".dp-demand").html(this.error_message(error));
		}
	}

	selected_vehicle_category() {
		return this.vehicle_category?.get_value() || null;
	}

	async refresh_transfers(request_id) {
		const transfer_request = (this.transfer_request_id = (this.transfer_request_id || 0) + 1);
		const target = this.$root.find(".dp-transfer-demand");
		this.$root.find(".dp-transfer-actions, .dp-transfer-pagination").prop("hidden", true);
		target.html(this.loading());
		try {
			const filters = this.get_filter_values();
			const response = await frappe.call({
				method: "vuna_transport.services.planning.transport_load_service.get_transfer_planning_items",
				args: {
					company: filters.company,
					source_warehouse: filters.source_warehouse,
					date_from: filters.required_delivery_date_from,
					date_to: filters.required_delivery_date_to,
					start: this.transfer_start,
				},
			});
			if (
				request_id !== this.refresh_request_id ||
				transfer_request !== this.transfer_request_id ||
				this.destroyed
			)
				return;
			this.transfer_items = response.message.items || [];
			this.set_movement_items("depots", this.transfer_items);

			this.$root.find(".dp-transfer-actions").prop("hidden", true);
			this.$root
				.find(".dp-transfer-pagination")
				.prop("hidden", !this.transfer_start && !response.message.has_more);
			this.$root.find(".btn-transfer-prev").prop("disabled", !this.transfer_start);
			this.$root.find(".btn-transfer-next").prop("disabled", !response.message.has_more);
			if (response.message.truncated)
				target.append(
					$("<p>").text(__("Results limited to 500 items. Refine your filters."))
				);
		} catch (error) {
			if (
				request_id === this.refresh_request_id &&
				transfer_request === this.transfer_request_id
			)
				target.html(this.error_message(error));
		}
	}

	async refresh_suppliers(request_id) {
		const version = (this.supplier_request_id = (this.supplier_request_id || 0) + 1);
		const target = this.$root.find(".dp-supplier-demand");
		this.$root.find(".dp-supplier-actions, .dp-supplier-pagination").prop("hidden", true);
		target.html(this.loading());
		try {
			const filters = this.get_filter_values();
			const { message } = await frappe.call({
				method: "vuna_transport.services.planning.supplier_load_service.get_planning_items",
				args: {
					company: filters.company,
					date_from: filters.required_delivery_date_from,
					date_to: filters.required_delivery_date_to,
					start: this.supplier_start,
				},
			});
			if (
				this.destroyed ||
				request_id !== this.refresh_request_id ||
				version !== this.supplier_request_id
			)
				return;
			this.supplier_items = message.items || [];
			this.set_movement_items("suppliers", this.supplier_items);

			this.$root.find(".dp-supplier-actions").prop("hidden", true);
			this.$root
				.find(".dp-supplier-pagination")
				.prop("hidden", !this.supplier_start && !message.has_more);
			this.$root.find(".btn-supplier-prev").prop("disabled", !this.supplier_start);
			this.$root.find(".btn-supplier-next").prop("disabled", !message.has_more);
			if (message.truncated)
				target.append(
					$("<p>").text(__("Results limited to 500 items. Refine your filters."))
				);
		} catch (error) {
			if (request_id === this.refresh_request_id && version === this.supplier_request_id)
				target.html(this.error_message(error));
		}
	}

	set_movement_items(tab, items) {
		const state = this.planning_state(tab);
		state.orders = [];
		state.details = {};
		for (const row of items) {
			const name = row.transport_demand;
			if (!state.details[name]) {
				const supplier = tab === "suppliers";
				state.orders.push({
					...row,
					name,
					customer_name: supplier ? row.supplier : row.destination_warehouse,
					delivery_address_summary: supplier
						? row.delivery_address_summary || __("Pickup address unavailable")
						: row.destination_warehouse,
					required_delivery_date: row.required_date,
				});
				state.details[name] = { name, items: [] };
			}
			state.details[name].items.push({ ...row, name: row.transport_demand_item });
		}
		for (const order of state.orders) {
			const rows = state.details[order.name].items;
			order.weight = rows.reduce((total, row) => total + flt(row.weight), 0);
			order.volume = rows.reduce((total, row) => total + flt(row.volume), 0);
		}
		// Reconcile selections on this page against refreshed live balances.
		for (const row of items) {
			const selected = state.allocations.get(row.transport_demand_item);
			if (selected)
				selected.allocated_quantity = Math.min(
					selected.allocated_quantity,
					row.available_quantity
				);
		}
		const visible_names = new Set(state.orders.map((order) => order.name));
		const visible_items = new Set(items.map((row) => row.transport_demand_item));
		for (const [key, selection] of state.allocations) {
			if (visible_names.has(selection.delivery_order) && !visible_items.has(key))
				state.allocations.delete(key);
		}
		if (this.active_planning_tab === tab) {
			this.plan = null;
			this.plan_evaluation_id += 1;
			this.cost_request_id += 1;
			this.render_demand();
			this.render_plan();
			this.schedule_plan_evaluation();
		}
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
		this.close_grouping_popover();
		if (!this.orders.length) {
			this.$root
				.find(this.demand_target())
				.html(`<div class="dp-empty">${__("No pending transport demand found.")}</div>`);
			this.render_pagination();
			return;
		}
		const rows = this.orders
			.map((order) => {
				const selected = this.order_selection_state(order.name).checked ? "checked" : "";
				const collapsed = this.collapsed_orders?.has(order.name) || false;
				const details_id = `dp-items-${encodeURIComponent(order.name)}`;
				const city = order.delivery_city || order.town;
				const destination =
					order.delivery_address_summary ||
					(order.delivery_address
						? __("Delivery address unavailable")
						: __("Delivery address not set"));
				const requirements = [
					order.refrigeration_required ? __("Refrigerated") : "",
					order.tail_lift_required ? __("Tail lift") : "",
				]
					.filter(Boolean)
					.join(", ");
				return `<section class="dp-order-group" aria-label="${this.escape(
					__("{0} — {1}", [order.name, order.customer_name || order.customer || ""])
				)}"><div class="dp-demand-row${
					order.vehicle_category_incompatible ? " dp-demand-row-warning" : ""
				}" data-order="${this.escape(order.name)}" title="${this.escape(
					order.vehicle_category_incompatible
						? __("Selected order is not compatible with this Vehicle Category.")
						: ""
				)}">
				<div class="dp-order-controls"><label class="dp-selection-label"><input type="checkbox" class="dp-order-select" title="${this.escape(
					__("Select order {0} and all its remaining items", [order.name])
				)}" aria-label="${this.escape(
					__("Select order {0}", [order.name])
				)}" ${selected}></label><button type="button" class="dp-order-toggle" data-order="${this.escape(
					order.name
				)}" aria-expanded="${!collapsed}" aria-controls="${this.escape(
					details_id
				)}" aria-label="${this.escape(
					collapsed
						? __("Expand items for {0}", [order.name])
						: __("Collapse items for {0}", [order.name])
				)}">${collapsed ? "▸" : "▾"}</button></div>
				<div class="dp-order-context"><div class="dp-order-identity"><strong class="dp-order-customer">${this.escape(
					order.customer_name || order.customer
				)}</strong>${
					city ? `<span class="dp-order-city">— ${this.escape(city)}</span>` : ""
				}${
					requirements
						? `<span class="dp-order-requirements">${this.escape(requirements)}</span>`
						: ""
				}</div>
				<div class="dp-order-locations"><span class="dp-order-destination" title="${this.escape(
					order.delivery_address_type
						? __("Linked address type: {0}", [order.delivery_address_type])
						: destination
				)}"><span class="dp-location-label">${
					this.active_planning_tab === "suppliers"
						? __("Collect from:")
						: __("Deliver to:")
				}</span> ${this.escape(destination)}</span></div>
				${this.render_grouping_details(order)}
				<div class="dp-order-meta"><a class="dp-order-reference" href="/app/${frappe.router.slug(
					this.customer_context.doctype
				)}/${encodeURIComponent(order.name)}">${this.escape(
					order.name
				)}</a><span>${this.escape(
					frappe.datetime.str_to_user(order.required_delivery_date)
				)}</span>${order.territory ? `<span>${this.escape(order.territory)}</span>` : ""}${
					["High", "Urgent"].includes(order.priority)
						? `<span class="dp-order-requirements">${
								order.priority === "Urgent"
									? __("Urgent priority")
									: __("High priority")
						  }</span>`
						: ""
				}</div>
				<div class="dp-order-transport"><span class="dp-order-origin"><span class="dp-location-label">${__(
					this.active_planning_tab === "suppliers" ? "To:" : "From:"
				)}</span> ${this.escape(
					this.active_planning_tab === "suppliers"
						? order.destination_warehouse
						: this.filters.source_warehouse.get_value() ||
								order.source_warehouse ||
								__("Warehouse not set")
				)}</span><span class="dp-order-capacity">${this.number(
					order.weight
				)} ${this.escape(order.weight_uom || "")} · ${this.number(
					order.volume
				)} ${this.escape(order.volume_uom || "")}</span></div></div>
			</div><div class="dp-item-details" id="${this.escape(details_id)}" data-details="${this.escape(
					order.name
				)}" ${collapsed ? "hidden" : ""}>${
					this.details[order.name]
						? this.render_item_table(this.details[order.name])
						: ""
				}</div></section>`;
			})
			.join("");
		this.$root
			.find(this.demand_target())
			.html(
				`<div class="dp-demand-header"><span><label class="dp-selection-label"><input type="checkbox" class="dp-select-all-orders" title="${__(
					"Select all visible demand"
				)}" aria-label="${__("Select all visible demand")}"><span>${__(
					"All"
				)}</span></label></span><span>${__("Item")}</span><span>${__(
					"Remaining"
				)}</span><span>${
					this.active_planning_tab === "suppliers"
						? __("Destination")
						: __("Stock on Hand")
				}</span><span>${__("Load Qty")}</span></div>${rows}`
			);
		this.update_select_all_state();
		this.render_pagination();
	}

	render_grouping_details(order) {
		const fields = [
			[__("Zone"), order.transport_zone],
			[__("Delivery Method"), order.delivery_method],
			[__("Delivery Group"), order.delivery_group],
			[__("Transport Compatibility Group"), order.transport_compatibility_group],
		];
		if (order.transport_handling_profile)
			fields.push([__("Handling Profile"), order.transport_handling_profile]);
		const tags = fields
			.filter(([, value]) => value)
			.map(
				([label, value]) =>
					`<span class="dp-grouping-tag" title="${this.escape(
						`${label}: ${value}`
					)}" aria-label="${this.escape(`${label}: ${value}`)}">${this.escape(
						value
					)}</span>`
			)
			.join("");
		return `<span class="dp-order-grouping">${tags}<button type="button" class="dp-grouping-trigger" aria-haspopup="dialog" aria-expanded="false" title="${__(
			"View grouping details"
		)}">${
			tags ? "" : this.escape(__("Grouping"))
		}<span class="dp-grouping-more" aria-hidden="true">⋯</span><span class="sr-only">${__(
			"View grouping details"
		)}</span></button><template class="dp-grouping-template"><dl class="dp-grouping-details">${fields
			.map(
				([label, value]) =>
					`<dt>${this.escape(label)}</dt><dd>${this.escape(value || __("Not set"))}</dd>`
			)
			.join("")}</dl></template></span>`;
	}

	show_grouping_popover(event) {
		const trigger = event.currentTarget;
		const already_open = this.grouping_trigger === trigger;
		this.close_grouping_popover();
		if (already_open) return;
		this.grouping_trigger = trigger;
		const $trigger = $(trigger);
		$trigger
			.attr("aria-expanded", "true")
			.popover({
				html: true,
				content: $trigger
					.closest(".dp-order-grouping")
					.find(".dp-grouping-template")
					.contents()
					.clone(),
				container: "body",
				placement: "bottom",
				boundary: "viewport",
				trigger: "manual",
				animation: false,
				template: `<div class="popover dp-grouping-popover" role="dialog" aria-label="${__(
					"Grouping details"
				)}"><div class="arrow"></div><div class="popover-body"></div></div>`,
			})
			.popover("show");
		this.grouping_outside_handler = (e) => {
			if (!trigger.contains(e.target) && !e.target.closest?.(".dp-grouping-popover"))
				this.close_grouping_popover();
		};
		this.grouping_key_handler = (e) => {
			if (e.key === "Escape") {
				e.preventDefault();
				this.close_grouping_popover(true);
			}
		};
		this.grouping_scroll_handler = () => this.close_grouping_popover();
		document.addEventListener("pointerdown", this.grouping_outside_handler);
		document.addEventListener("keydown", this.grouping_key_handler);
		window.addEventListener("resize", this.grouping_scroll_handler);
		this.$root
			.find(".dp-demand, .dp-transfer-demand, .dp-supplier-demand")
			.on("scroll.dp-grouping", this.grouping_scroll_handler);
	}

	close_grouping_popover(restore_focus = false) {
		if (!this.grouping_trigger) return;
		const trigger = this.grouping_trigger;
		this.grouping_trigger = null;
		document.removeEventListener("pointerdown", this.grouping_outside_handler);
		document.removeEventListener("keydown", this.grouping_key_handler);
		window.removeEventListener("resize", this.grouping_scroll_handler);
		this.$root
			.find(".dp-demand, .dp-transfer-demand, .dp-supplier-demand")
			.off("scroll.dp-grouping");
		$(trigger).attr("aria-expanded", "false").popover("dispose");
		if (restore_focus && trigger.isConnected) trigger.focus();
	}

	render_pagination() {
		if (this.active_planning_tab && this.active_planning_tab !== "customers") return;
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

	toggle_order_details(event) {
		const $button = $(event.currentTarget);
		const name = $button.data("order");
		if (!this.collapsed_orders) this.collapsed_orders = new Set();
		const collapsed = !this.collapsed_orders.has(name);
		if (collapsed) this.collapsed_orders.add(name);
		else this.collapsed_orders.delete(name);
		$button
			.attr({
				"aria-expanded": String(!collapsed),
				"aria-label": collapsed
					? __("Expand items for {0}", [name])
					: __("Collapse items for {0}", [name]),
			})
			.text(collapsed ? "▸" : "▾");
		this.$root.find(`[data-details="${this.selector(name)}"]`).prop("hidden", collapsed);
	}

	async toggle_order(event) {
		const state = this.planning_state();
		const $row = $(event.currentTarget).closest(".dp-demand-row");
		const name = $row.data("order");
		if (event.currentTarget.checked) {
			await this.ensure_details([name]);
			if (this.planning_state() !== state) return;
			for (const item of this.details[name]?.items || []) {
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
		this.render_demand();
	}

	async toggle_all_orders(event) {
		const state = this.planning_state();
		const names = this.orders.map((order) => order.name);
		if (!names.length) return;
		if (event.currentTarget.checked) {
			await this.ensure_details(names);
			if (this.planning_state() !== state) return;
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

	order_selection_state(name) {
		const items = (this.details[name]?.items || []).filter(
			(item) => item.available_quantity > 0
		);
		const selected = items.filter((item) => this.allocations.has(item.name)).length;
		return {
			checked: items.length > 0 && selected === items.length,
			indeterminate: selected > 0 && selected < items.length,
			disabled: !items.length,
		};
	}

	update_select_all_state() {
		const states = this.orders.map((order) => this.order_selection_state(order.name));
		for (const order of this.orders) {
			const checkbox = this.$root
				.find(`[data-order="${this.selector(order.name)}"] .dp-order-select`)
				.get(0);
			if (checkbox) Object.assign(checkbox, this.order_selection_state(order.name));
		}
		const eligible = states.filter((state) => !state.disabled);
		const checkbox = this.$root.find(`${this.demand_target()} .dp-select-all-orders`).get(0);
		if (!checkbox) return;
		checkbox.checked = eligible.length > 0 && eligible.every((state) => state.checked);
		checkbox.indeterminate =
			!checkbox.checked && eligible.some((state) => state.checked || state.indeterminate);
		checkbox.disabled = !eligible.length;
	}

	toggle_item(event) {
		const $checkbox = $(event.currentTarget);
		const name = $checkbox.data("item");
		const order = $checkbox.data("order");
		const item = this.details[order]?.items.find((row) => row.name === name);
		if (!item || item.available_quantity <= 0) return;
		if (event.currentTarget.checked) {
			this.allocations.set(name, {
				delivery_order: order,
				delivery_order_item: name,
				allocated_quantity: item.available_quantity,
			});
		} else this.allocations.delete(name);
		this.$root
			.find(`.dp-allocation-qty[data-item="${this.selector(name)}"]`)
			.val(this.allocations.get(name)?.allocated_quantity || 0);
		this.plan_evaluation_id += 1;
		this.plan = null;
		this.clear_suggestions();
		this.render_plan();
		this.schedule_plan_evaluation();
		this.update_select_all_state();
	}

	async ensure_details(names, state = this.planning_state()) {
		const missing = names.filter((name) => !state.details[name]);
		if (!missing.length) return;
		const request_key = [...missing].sort().join("\u0000");
		const requests = state.details_requests;
		const details = state.details;
		if (!requests.has(request_key)) {
			requests.set(
				request_key,
				this.call("get_delivery_order_details", {
					delivery_order_names: missing,
				})
					.then((rows) => {
						if (this.destroyed || state.details !== details) return;
						for (const row of rows) details[row.name] = row;
						return rows;
					})
					.finally(() => requests.delete(request_key))
			);
		}
		await requests.get(request_key);
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
		return `<div class="dp-items">${
			restrictions ? `<div class="dp-item-heading">${this.escape(restrictions)}</div>` : ""
		}${order.items
			.map((item) => {
				const allocation = this.allocations.get(item.name);
				const uom = this.escape(item.uom || item.stock_uom || "");
				return `<div class="dp-item-row"><span><label class="dp-selection-label"><input type="checkbox" class="dp-item-select" data-order="${this.escape(
					order.name
				)}" data-item="${this.escape(item.name)}" aria-label="${this.escape(
					__("Select item {0} from order {1}", [item.item_code, order.name])
				)}" title="${this.escape(__("Select item {0}", [item.item_code]))}" ${
					allocation ? "checked" : ""
				} ${
					item.available_quantity > 0 ? "" : "disabled"
				}></label></span><span><strong>${this.escape(
					item.item_code
				)}</strong><small>${this.escape(
					item.item_name && item.item_name !== item.item_code ? item.item_name : ""
				)}</small></span><span>${this.number(
					item.available_quantity
				)} ${uom}</span><span>${
					this.active_planning_tab === "suppliers"
						? this.escape(item.destination_warehouse)
						: this.render_stock(item)
				}</span><span class="dp-qty-entry"><input type="number" class="form-control dp-allocation-qty" aria-label="${this.escape(
					__("Quantity to load for {0} ({1})", [
						item.item_code,
						item.uom || item.stock_uom,
					])
				)}" ${item.available_quantity > 0 ? "" : "disabled"} placeholder="${__(
					"Enter quantity"
				)}" title="${__(
					"Enter the quantity to include in this Transport Load"
				)}" data-order="${this.escape(order.name)}" data-item="${this.escape(
					item.name
				)}" min="0" max="${item.available_quantity}" step="any" value="${
					allocation?.allocated_quantity || 0
				}"><small>${uom}</small></span></div>`;
			})
			.join("")}</div>`;
	}

	render_stock(item) {
		if (!item.stock_warehouse)
			return `<span class="text-muted">${__("Select a warehouse")}</span>`;
		const stock = this.number(item.stock_available_quantity);
		const uom = this.escape(item.stock_uom || "");
		const factor = flt(item.conversion_factor);
		const converted = factor > 0 && item.uom && item.uom !== item.stock_uom;
		const quantity = converted
			? `${this.number(item.stock_available_quantity / factor)} ${this.escape(item.uom)}`
			: `${stock} ${uom}`;
		const sufficient = item.stock_availability_status === "Available";
		const insufficient = item.stock_availability_status === "Insufficient";
		const status = sufficient
			? __("Stock meets remaining demand")
			: insufficient
			? __("Stock is below remaining demand")
			: "";
		const color = sufficient
			? "dp-stock-available"
			: insufficient
			? "dp-stock-insufficient"
			: "";
		return `<span title="${this.escape(
			[item.stock_warehouse, status].filter(Boolean).join(" — ")
		)}"><span class="${color}">${quantity}${
			status ? `<span class="sr-only"> — ${this.escape(status)}</span>` : ""
		}</span><span class="dp-stock-warehouse">${this.escape(
			item.stock_warehouse
		)}</span></span>`;
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
		this.$root
			.find(`.dp-item-select[data-item="${this.selector(item)}"]`)
			.prop("checked", quantity > 0);
		this.plan_evaluation_id += 1;
		this.plan = null;
		this.clear_suggestions();
		this.render_plan();
		this.schedule_plan_evaluation();
		this.update_select_all_state();
	}

	render_plan() {
		this.cost_request_id += 1;
		const rows = [...this.allocations.values()];
		const $content = this.$root.find(".dp-plan-content");
		const $metrics = this.$root.find(".dp-plan-metrics").empty();
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
				.html(__("Select customer demand to begin planning."));
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
		$metrics.html(
			`<span><strong>${this.plan.total_stops}</strong>${__(
				"Stops"
			)}</span><span><strong>${this.number(totals.weight)}</strong>${this.escape(
				this.plan.weight_uom || __("Weight")
			)}</span><span><strong>${this.number(totals.volume)}</strong>${this.escape(
				this.plan.volume_uom || __("Volume")
			)}</span>`
		);
		$content
			.removeClass("dp-empty")
			.html(
				`${this.render_capacity_breakdown(capacity)}${this.render_capacity_override(
					category_row
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
			const warning_html = (response?.warnings || [])
				.map((warning) => `<div class="text-warning small">${this.escape(warning)}</div>`)
				.join("");
			$target.html(
				candidates.length
					? `${warning_html}<div class="small text-muted">${__(
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

	render_capacity_override(category) {
		if (!category) return "";
		const percentage = Number(category.capacity_override_percentage) || 0;
		if (category.capacity_override_applied && percentage > 0) {
			const using_override = Object.values(category.capacity || {}).some(
				(values) => values.base_capacity > 0 && values.used > values.base_capacity
			);
			return `<div class="small ${
				using_override ? "text-warning" : "text-muted"
			} dp-capacity-override">${__(
				using_override
					? "Using capacity override (+{0}%)"
					: "Capacity includes {0}% override allowance",
				[this.number(percentage)]
			)}</div>`;
		}
		if (category.capacity_override_allowed && !percentage)
			return `<div class="small text-muted dp-capacity-override">${__(
				"No capacity override allowance configured for this category."
			)}</div>`;
		if (percentage > 0)
			return `<div class="small text-muted dp-capacity-override">${__(
				"Capacity override is disabled or not permitted for your user."
			)}</div>`;
		return "";
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
		this.$root.find(".btn-create-load").prop("disabled", true);
		try {
			const plan = await this.call("evaluate_manual_load_plan", {
				allocations: rows,
				vehicle_category: this.selected_vehicle_category(),
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
				this.selected_vehicle_category() || this.plan.recommended_vehicle_category;
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
		this.$root.find(".dp-suggestions-section").prop("open", true);
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
					(row) => row[this.customer_context.order_field] === order.name
				);
				const partial = allocations.some((row) => row.partial);
				return `<div class="dp-suggestion-order">
				<span><a href="/app/${frappe.router.slug(this.customer_context.doctype)}/${encodeURIComponent(
					order.name
				)}"><strong>${this.escape(order.name)}</strong></a><small>${this.escape(
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
			${this.render_capacity_override(suggestion)}
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
						this.customer_context.doctype
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
			this.allocations.set(row[this.customer_context.item_field], {
				delivery_order: row[this.customer_context.order_field],
				delivery_order_item: row[this.customer_context.item_field],
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
		this.$root.find(".dp-suggestions-section").prop("open", false);
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
		// Internal selection keys are presentation-only. Send the server's explicit
		// Link dialect, never Transport Demand IDs in Delivery Order Link fields.
		if (args?.allocations) {
			const context =
				this.active_planning_tab && this.active_planning_tab !== "customers"
					? { order_field: "transport_demand", item_field: "transport_demand_item" }
					: this.customer_context;
			args = {
				...args,
				allocations: args.allocations.map((row) => ({
					[context.order_field]: row.delivery_order,
					[context.item_field]: row.delivery_order_item,
					allocated_quantity: row.allocated_quantity,
				})),
				source_warehouse:
					this.active_planning_tab === "suppliers"
						? null
						: this.filters.source_warehouse.get_value() || null,
			};
		}
		if (method === "get_delivery_order_details")
			args = {
				...args,
				source_warehouse: this.filters.source_warehouse.get_value() || null,
			};
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
