// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vehicle Allocation", {
	setup(frm) {
		frm.set_query("cost_structure", () => ({
			filters: {
				docstatus: 1,
				disabled: 0,
				...(frm.doc.company ? { company: ["in", [frm.doc.company, "", null]] } : {}),
			},
		}));
		frm.set_query("delivery_load", "delivery_loads", () => {
			if (!require_vehicle_category(frm)) return { filters: { name: ["in", []] } };
			return {
				query: "vuna_transport.api.get_delivery_loads_for_allocation",
				filters: {
					company: frm.doc.company,
					vehicle: frm.doc.vehicle,
					required_vehicle_category: frm.doc.required_vehicle_category,
					selected_delivery_loads: (frm.doc.delivery_loads || [])
						.map((row) => row.delivery_load)
						.filter(Boolean),
				},
			};
		});
		frm.set_query("vehicle", () => {
			return {
				query: "vuna_transport.api.get_vehicles_for_allocation",
				filters: {
					company: frm.doc.company,
					required_vehicle_category: frm.doc.required_vehicle_category,
					delivery_loads: (frm.doc.delivery_loads || [])
						.map((row) => row.delivery_load)
						.filter(Boolean),
				},
			};
		});
		frm.set_query("driver", () => ({
			query: "vuna_transport.api.get_drivers_for_allocation",
			filters: {
				company: frm.doc.company,
				delivery_loads: (frm.doc.delivery_loads || [])
					.map((row) => row.delivery_load)
					.filter(Boolean),
			},
		}));
	},

	refresh(frm) {
		render_allocation_cost_breakdown(frm);
		refresh_allocation_items(frm);
		// Transport Loads are retained as the demand record. The server-side
		// cancellation flow detaches this allocation before Frappe checks links.
		frm.ignore_doctypes_on_cancel_all = ["Transport Load"];
		set_allocation_execution_indicator(frm, frm.doc.status || "Draft");
		update_load_selection_button(frm);
		frm.remove_custom_button(__("Delivery Notes"), __("Create"));
		frm.remove_custom_button(__("Delivery Trip"), __("Create"));
		frm.remove_custom_button(__("Stock Entry (Add to Transit)"), __("Create"));
		const loads = (frm.doc.delivery_loads || [])
			.map((row) => row.delivery_load)
			.filter(Boolean);
		if (frm.doc.docstatus === 1 && loads.length) {
			add_next_execution_action(frm);
		}
		if (loads.length) {
			frappe
				.call({
					method: "vuna_transport.services.fleet.vehicle_allocation_service.get_allocation_load_context",
					args: {
						delivery_loads: loads,
						vehicle_category: frm.doc.required_vehicle_category,
					},
				})
				.then(({ message }) => render_capacity_summary(frm, message));
		}
	},

	vehicle(frm) {
		const vehicle_name = frm.doc.vehicle;
		if (!vehicle_name) return;
		return frappe.db
			.get_value("Vehicle", vehicle_name, [
				"vuna_supplier",
				"vuna_vehicle_owner",
				"vuna_vehicle_category",
				"vuna_transport_cost_structure",
			])
			.then(({ message }) => {
				if (!message || frm.doc.vehicle !== vehicle_name) return;
				if (message.vuna_vehicle_category !== frm.doc.required_vehicle_category)
					frm.set_value(
						"required_vehicle_category",
						message.vuna_vehicle_category || null
					);
				frm.set_value("vehicle_owner", message.vuna_vehicle_owner || null);
				frm.set_value(
					"allocation_type",
					message.vuna_vehicle_owner === "Supplier" ? "Subcontractor" : "Owned Fleet"
				);
				frm.set_value("supplier", message.vuna_supplier || null);
				frm.set_value("cost_structure", message.vuna_transport_cost_structure || null);
			});
	},

	required_vehicle_category(frm) {
		update_load_selection_button(frm);
		if (frm.doc.vehicle) frm.trigger("vehicle");
	},

	allocation_type(frm) {
		frm.toggle_reqd(
			"vehicle",
			["Owned Fleet", "Subcontractor"].includes(frm.doc.allocation_type)
		);
		frm.toggle_reqd("driver", true);
	},

	cost_structure(frm) {
		if (!frm.doc.vehicle || !frm.doc.cost_structure) return;
		frappe.db
			.get_value("Vehicle", frm.doc.vehicle, "vuna_transport_cost_structure")
			.then(({ message }) => {
				if (message?.vuna_transport_cost_structure === frm.doc.cost_structure) return;
				frm.set_value("cost_structure", message?.vuna_transport_cost_structure || null);
				frappe.msgprint(
					__(
						"The selected cost structure must match the Vehicle's configured Transport Cost Structure."
					)
				);
			});
	},
});

function add_next_execution_action(frm) {
	frappe
		.call({
			method: "vuna_transport.services.fleet.vehicle_allocation_service.get_allocation_delivery_note_status",
			args: { allocation_name: frm.doc.name },
		})
		.then(({ message }) => {
			if (frm.doc.docstatus !== 1 || !message) return;
			set_allocation_execution_indicator(frm, message.execution_status);
			if (message.active_delivery_trip) return;
			const pending_transfers = (message.transfer_stock || []).filter(
				(row) => !row.submitted
			);
			if (pending_transfers.length) {
				frm.add_custom_button(
					__("Stock Entry (Add to Transit)"),
					() => {
						const open_stock = (load_name) =>
							frappe
								.call({
									method: "vuna_transport.services.execution.allocation_transfer_service.make_allocation_stock_entry",
									args: { source_name: frm.doc.name, load_name },
									freeze: true,
								})
								.then(({ message: doc }) => {
									const mapped = frappe.model.sync(doc)[0];
									frappe.set_route("Form", mapped.doctype, mapped.name);
								});
						if (pending_transfers.length === 1) open_stock(pending_transfers[0].load);
						else
							frappe.prompt(
								{
									fieldname: "load",
									fieldtype: "Select",
									label: __("Transport Load"),
									options: pending_transfers.map((row) => row.load),
									reqd: true,
								},
								(values) => open_stock(values.load)
							);
					},
					__("Create")
				);
				frm.page.set_inner_btn_group_as_primary(__("Create"));
				if (!(message.draft_delivery_notes || []).length && message.task_based) return;
			}
			if (message.ready_for_delivery_trip) {
				frm.add_custom_button(
					__("Delivery Trip"),
					() =>
						frappe.model.open_mapped_doc({
							method: "vuna_transport.services.fleet.vehicle_allocation_service.make_delivery_trip_from_allocation",
							frm,
							freeze_message: __("Preparing Delivery Trip..."),
						}),
					__("Create")
				);
				frm.page.set_inner_btn_group_as_primary(__("Create"));
				return;
			}

			frm.add_custom_button(
				__("Delivery Notes"),
				() =>
					frm.call("create_delivery_notes").then(({ message: delivery_notes }) => {
						if (!delivery_notes) return;
						if (delivery_notes.length === 1) {
							frappe.set_route("Form", "Delivery Note", delivery_notes[0]);
						} else {
							frappe.set_route("List", "Delivery Note", {
								name: ["in", delivery_notes],
							});
						}
					}),
				__("Create")
			);
			frm.page.set_inner_btn_group_as_primary(__("Create"));
		});
}

function set_allocation_execution_indicator(frm, status) {
	const indicators = {
		Draft: "gray",
		Allocated: "green",
		Accepted: "green",
		Rejected: "red",
		"Awaiting Delivery Notes": "orange",
		"Awaiting Transfer Stock Entry": "orange",
		"Ready for Delivery Trip": "blue",
		"Delivery Trip Draft": "orange",
		"Delivery Trip Scheduled": "blue",
		"In Transit": "blue",
		Completed: "green",
		Replaced: "gray",
		Cancelled: "red",
	};
	frm.page.set_indicator(__(status), indicators[status] || "gray");
}

function require_vehicle_category(frm) {
	if (frm.doc.required_vehicle_category) return true;
	const field = frm.doc.vehicle ? "required_vehicle_category" : "vehicle";
	frappe.msgprint({
		title: __("Vehicle or Category Required"),
		message: __(
			"Select a Vehicle to fill its category, or select Vehicle Category before choosing Transport Loads."
		),
		indicator: "orange",
		primary_action: {
			label: frm.doc.vehicle ? __("Select Vehicle Category") : __("Select Vehicle"),
			action() {
				frappe.hide_msgprint();
				frm.scroll_to_field(field);
			},
		},
	});
	frm.scroll_to_field(field);
	return false;
}

function select_delivery_loads(frm) {
	if (!require_vehicle_category(frm)) return;
	if (!frm.doc.company) {
		frappe.msgprint(__("Select a Company before selecting Transport Loads."));
		return;
	}

	new frappe.ui.form.MultiSelectDialog({
		doctype: "Transport Load",
		target: frm,
		size: "extra-large",
		add_filters_group: 1,
		columns: ["name", "company", "required_vehicle_category"],
		setters: {
			company: frm.doc.company,
			required_vehicle_category: frm.doc.required_vehicle_category,
		},
		get_query() {
			if (!require_vehicle_category(frm)) return { filters: { name: ["in", []] } };
			return {
				query: "vuna_transport.api.get_delivery_loads_for_allocation",
				filters: {
					company: frm.doc.company,
					vehicle: frm.doc.vehicle,
					required_vehicle_category: frm.doc.required_vehicle_category,
					selected_delivery_loads: (frm.doc.delivery_loads || [])
						.map((row) => row.delivery_load)
						.filter(Boolean),
				},
			};
		},
		action(selections) {
			if (!require_vehicle_category(frm)) return;
			if (!selections.length) {
				frappe.msgprint(__("Select at least one Transport Load."));
				return;
			}
			const existing = new Set(
				(frm.doc.delivery_loads || []).map((row) => row.delivery_load).filter(Boolean)
			);
			selections.forEach((name) => {
				if (!existing.has(name)) frm.add_child("delivery_loads", { delivery_load: name });
			});
			frm.refresh_field("delivery_loads");
			refresh_load_context(frm);
			cur_dialog.hide();
		},
	});
}

function update_load_selection_button(frm) {
	frm.remove_custom_button(__("Select Transport Loads"), __("Planning"));
	if (frm.doc.docstatus === 0) {
		frm.add_custom_button(
			__("Select Transport Loads"),
			() => select_delivery_loads(frm),
			__("Planning")
		);
	}
}

function refresh_load_context(frm) {
	refresh_allocation_items(frm);
	const loads = (frm.doc.delivery_loads || []).map((row) => row.delivery_load).filter(Boolean);
	if (!loads.length) return;
	frappe
		.call({
			method: "vuna_transport.services.fleet.vehicle_allocation_service.get_allocation_load_context",
			args: { delivery_loads: loads, vehicle_category: frm.doc.required_vehicle_category },
		})
		.then(({ message }) => {
			if (!message) return;
			frm.set_value("company", message.company || null);
			if (!frm.doc.required_vehicle_category) {
				frm.set_value(
					"required_vehicle_category",
					message.required_vehicle_category || null
				);
			}
			if (!frm.doc.planned_reporting_time && message.planned_reporting_time)
				frm.set_value("planned_reporting_time", message.planned_reporting_time);
			render_capacity_summary(frm, message);
		});
}

frappe.ui.form.on("Vehicle Allocation Load", {
	delivery_load(frm, cdt, cdn) {
		if (!frappe.get_doc(cdt, cdn).delivery_load) {
			refresh_load_context(frm);
			return;
		}
		if (!require_vehicle_category(frm)) {
			frappe.model.set_value(cdt, cdn, "delivery_load", null);
			return;
		}
		refresh_allocation_items(frm);
		frappe
			.call({
				method: "vuna_transport.services.fleet.vehicle_allocation_service.get_allocation_load_context",
				args: {
					vehicle_category: frm.doc.required_vehicle_category,
					delivery_loads: (frm.doc.delivery_loads || [])
						.map((row) => row.delivery_load)
						.filter(Boolean),
				},
			})
			.then(({ message }) => {
				if (!message) return;
				frm.set_value("company", message.company || null);
				if (!frm.doc.required_vehicle_category)
					frm.set_value(
						"required_vehicle_category",
						message.required_vehicle_category || null
					);
				if (!frm.doc.planned_reporting_time && message.planned_reporting_time) {
					frm.set_value("planned_reporting_time", message.planned_reporting_time);
				}
				render_capacity_summary(frm, message);
				frm.refresh_field("delivery_loads");
			});
	},

	delivery_loads_remove(frm) {
		refresh_allocation_items(frm);
		frappe
			.call({
				method: "vuna_transport.services.fleet.vehicle_allocation_service.get_allocation_load_context",
				args: {
					vehicle_category: frm.doc.required_vehicle_category,
					delivery_loads: (frm.doc.delivery_loads || [])
						.map((row) => row.delivery_load)
						.filter(Boolean),
				},
			})
			.then(({ message }) => {
				frm.set_value("company", message?.company || null);
				if (!frm.doc.required_vehicle_category)
					frm.set_value(
						"required_vehicle_category",
						message?.required_vehicle_category || null
					);
				render_capacity_summary(frm, message);
			});
	},
});

async function refresh_allocation_items(frm) {
	// Never add unsaved child rows to a submitted document merely by opening it.
	if (frm.doc.docstatus !== 0) return;
	const request = (frm._allocation_items_request = (frm._allocation_items_request || 0) + 1);
	const names = () =>
		(frm.doc.delivery_loads || []).map((row) => row.delivery_load).filter(Boolean);
	const loads = names();
	const fields = [
		"item_code",
		"item_name",
		"qty",
		"uom",
		"weight",
		"weight_uom",
		"transport_load",
	];
	try {
		const rows = loads.length
			? (
					await frappe.call({
						method: "vuna_transport.services.fleet.allocation_cargo_service.get_allocation_items",
						args: { load_names: loads },
					})
			  ).message || []
			: [];
		if (
			frm.doc.docstatus !== 0 ||
			request !== frm._allocation_items_request ||
			JSON.stringify(loads) !== JSON.stringify(names())
		)
			return;
		const values = (row) => fields.map((field) => row[field] ?? null);
		if (
			JSON.stringify((frm.doc.loaded_items || []).map(values)) ===
			JSON.stringify(rows.map(values))
		)
			return;
		frm.clear_table("loaded_items");
		rows.forEach((row) => frm.add_child("loaded_items", row));
		frm.refresh_field("loaded_items");
		if (frm.doc.docstatus === 0) frm.dirty();
	} catch (error) {
		if (request !== frm._allocation_items_request) return;
		frappe.show_alert({
			message: __("Unable to load allocation items. Reload the form to retry."),
			indicator: "orange",
		});
	}
}

function render_allocation_cost_breakdown(frm) {
	let components = frm.doc.cost_breakdown || [];
	if (typeof components === "string") {
		try {
			components = JSON.parse(components);
		} catch {
			components = [];
		}
	}
	if (!Array.isArray(components)) components = [];
	frm.toggle_display(
		"costs_tab",
		!!(components.length || frm.doc.estimated_on || frm.doc.cost_warnings)
	);
	const wrapper = frm.get_field("cost_breakdown_preview")?.$wrapper;
	if (!wrapper) return;
	if (!components.length) {
		wrapper.text(__("No component estimate is available."));
		return;
	}
	const escape = (value) => frappe.utils.escape_html(String(value ?? ""));
	const headers = ["Cost Component", "Basis", "Quantity", "UOM", "Rate", "Amount"];
	const rows = components
		.filter((row) => row && typeof row === "object")
		.map(
			(row) => `<tr>
		<td>${escape(row.component)}</td><td>${escape(row.basis)}</td>
		<td class="text-right">${frappe.format(flt(row.quantity), { fieldtype: "Float" })}</td>
		<td>${escape(row.uom)}</td>
		<td class="text-right">${frappe.format(
			flt(row.rate),
			{ fieldtype: "Currency", options: "estimated_cost_currency" },
			{ inline: true },
			frm.doc
		)}</td>
		<td class="text-right">${frappe.format(
			flt(row.amount),
			{ fieldtype: "Currency", options: "estimated_cost_currency" },
			{ inline: true },
			frm.doc
		)}</td>
	</tr>`
		)
		.join("");
	wrapper.html(
		`<div class="table-responsive"><table class="table table-bordered table-condensed"><thead><tr>${headers
			.map((label) => `<th>${escape(__(label))}</th>`)
			.join("")}</tr></thead><tbody>${rows}</tbody></table></div>`
	);
}

function render_capacity_summary(frm, context) {
	const $wrapper = frm.get_field("allocation_capacity_summary")?.$wrapper;
	if (!$wrapper) return;
	if (!context?.totals) {
		$wrapper.empty();
		return;
	}
	const totals = context.totals;
	const capacity = context.capacity?.capacity || {};
	const number = (value) => frappe.format(value || 0, { fieldtype: "Float" });
	const status_class = (utilization) => {
		if (utilization >= 100) return "red";
		if (utilization >= 70) return "green";
		return "blue";
	};
	const rows = [
		[__("Weight"), totals.weight, totals.weight_uom, capacity.weight],
		[__("Volume"), totals.volume, totals.volume_uom, capacity.volume],
	]
		.filter(([, , , detail]) => detail?.capacity)
		.map(([label, used, _uom, detail]) => {
			const indicator = status_class(detail.utilization);
			return `<tr><td class="text-muted">${label}</td><td>${number(used)}</td><td>${number(
				detail.capacity
			)}</td><td class="${indicator}">${number(
				detail.remaining
			)}</td><td><span class="indicator-pill ${indicator}">${number(
				detail.utilization
			)}%</span></td></tr>`;
		})
		.join("");
	$wrapper.html(
		`<div class="vuna-capacity-summary"><table class="table table-bordered table-condensed" style="margin:0"><thead><tr><th>${__(
			"Dimension"
		)}</th><th>${__("Occupied")}</th><th>${__("Capacity")}</th><th>${__(
			"Available"
		)}</th><th style="width:110px">${__(
			"Used"
		)}</th></tr></thead><tbody>${rows}</tbody></table></div>`
	);
}
