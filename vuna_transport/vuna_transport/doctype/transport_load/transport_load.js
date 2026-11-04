// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Transport Load", {
	setup(frm) {
		frm.set_query("transport_demand", "delivery_orders", () => ({
			filters: {
				docstatus: 1,
				company: frm.doc.company,
				source_warehouse: frm.doc.source_warehouse,
				movement_type: "Warehouse Transfer",
			},
		}));
		frm.set_query("transport_demand_item", "delivery_orders", (doc, cdt, cdn) => ({
			filters: {
				parent: locals[cdt][cdn].transport_demand || "",
				parenttype: "Transport Demand",
			},
		}));
		frm.set_query("required_vehicle_category", () => ({
			query: "vuna_transport.services.planning.delivery_load_service.get_compatible_vehicle_categories",
			filters: {
				total_weight: frm.doc.total_weight || 0,
				total_volume: frm.doc.total_volume || 0,
				total_pallets: frm.doc.total_pallets || 0,
				total_cases: frm.doc.total_cases || 0,
				total_crates: frm.doc.total_crates || 0,
				weight_uom: frm.doc.weight_uom || null,
				volume_uom: frm.doc.volume_uom || null,
				refrigeration_required: frm.doc.refrigeration_required ? 1 : 0,
				tail_lift_required: frm.doc.tail_lift_required ? 1 : 0,
				required_body_type: frm.doc.required_body_type || null,
				special_equipment_required: frm.doc.special_equipment_required || null,
				delivery_order_names: (frm.doc.delivery_orders || [])
					.map((row) => row.delivery_order)
					.filter(Boolean),
			},
		}));
		frm.set_query("transport_route", () => ({
			filters: {
				disabled: 0,
				...(frm.doc.transport_zone ? { transport_zone: frm.doc.transport_zone } : {}),
			},
		}));
		frm.set_query("delivery_order_item", "delivery_orders", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return {
				filters: row.delivery_order ? { parent: row.delivery_order } : {},
			};
		});
	},

	transport_route(frm) {
		if (!frm.doc.transport_route) return;
		frappe.db
			.get_value("Transport Route", frm.doc.transport_route, [
				"estimated_distance",
				"estimated_duration",
			])
			.then(({ message }) => {
				if (!message) return;
				if (!frm.doc.estimated_distance)
					frm.set_value("estimated_distance", message.estimated_distance);
				if (!frm.doc.estimated_duration)
					frm.set_value("estimated_duration", message.estimated_duration);
			});
	},

	required_vehicle_category(frm) {
		render_capacity_summary(frm);
	},

	refresh(frm) {
		// Downstream documents retain their audit lineage. The server-side
		// cancellation guard still blocks cancellation when submitted stock or
		// execution documents make it unsafe.
		frm.ignore_doctypes_on_cancel_all = [
			"Vehicle Allocation",
			"Pick List",
			"Delivery Note",
			"Delivery Trip",
		];
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(
				__("Get Items"),
				() => {
					new frappe.ui.form.MultiSelectDialog({
						doctype: "Delivery Order",
						target: frm,
						size: "extra-large",
						add_filters_group: 1,
						columns: [
							"name",
							"customer_name",
							"delivery_group",
							"transport_compatibility_group",
							"required_delivery_date",
							"requested_quantity",
							"reserved_quantity",
							"allocated_quantity",
							"unallocated_quantity",
						],
						setters: {
							company: frm.doc.company || null,
							source_warehouse: frm.doc.source_warehouse || null,
							transport_zone: frm.doc.transport_zone || null,
							transport_compatibility_group:
								frm.doc.delivery_orders?.[0]?.transport_compatibility_group ||
								null,
						},
						get_query() {
							return {
								query: "vuna_transport.services.planning.delivery_load_service.get_available_delivery_orders",
								filters: {
									docstatus: 1,
									status: ["in", ["Open", "Partially Allocated"]],
								},
							};
						},
						action(selections) {
							if (!selections.length) {
								frappe.msgprint(__("Select at least one Delivery Order."));
								return;
							}
							frappe
								.call({
									method: "vuna_transport.services.planning.delivery_load_service.get_delivery_order_items",
									args: {
										delivery_order_names: selections,
										source_warehouse: frm.doc.source_warehouse,
									},
								})
								.then((response) => {
									const result = response.message;
									if (!result) return;
									show_item_selection_dialog(frm, result);
								});
							cur_dialog.hide();
						},
					});
				},
				__("Planning")
			);
		}

		if (frm.is_new()) {
			render_new_load_progress(frm);
			render_capacity_summary(frm);
			return;
		}

		render_capacity_summary(frm);
		frm.trigger("load_execution_progress");
	},

	async load_execution_progress(frm) {
		try {
			const response = await frappe.call({
				method: "vuna_transport.services.execution.execution_service.get_load_execution_summary",
				args: { load: frm.doc.name },
			});
			if (!response.message) return;
			if (response.message.planning_only) {
				const allocation = response.message.vehicle_allocation;
				if (
					frm.doc.docstatus === 1 &&
					!["Cancelled", "Completed", "Completed with Exceptions"].includes(
						frm.doc.status
					)
				) {
					if (!allocation) {
						frm.add_custom_button(__("Allocate Vehicle"), () =>
							frappe.model.open_mapped_doc({
								method: "vuna_transport.services.fleet.vehicle_allocation_service.make_vehicle_allocation_from_load",
								frm,
							})
						).addClass("btn-primary");
					} else if (allocation.docstatus === 0) {
						frm.add_custom_button(__("Complete Vehicle Allocation"), () =>
							frappe.set_route("Form", "Vehicle Allocation", allocation.name)
						);
					} else if (!response.message.delivery_trip) {
						frm.add_custom_button(
							__("Delivery Trip"),
							() =>
								frappe.model.open_mapped_doc({
									method: "vuna_transport.services.fleet.vehicle_allocation_service.make_delivery_trip_from_allocation",
									source_name: allocation.name,
								}),
							__("Create")
						);
					}
				}
				frm.get_field("execution_overview")?.$wrapper.text(
					__(
						"Allocate the vehicle, then create its Delivery Trip. Transfer proof and stock execution will follow."
					)
				);
				return;
			}
			frm.execution_summary = response.message;
			add_execution_actions(frm, response.message);
			render_execution_progress(frm, response.message);
		} catch (error) {
			console.error("Unable to load Transport Load execution progress", error);
			frm.get_field("execution_overview")?.$wrapper.html(
				`<div class="alert alert-warning">${__(
					"Execution progress could not be loaded. Reload the form or use the available action above."
				)}</div>`
			);
		}
	},
});

function add_execution_actions(frm, summary) {
	const action = summary.next_action;
	if (frm.doc.docstatus === 1 && !["completed", "cancelled"].includes(action.key)) {
		frm.remove_custom_button(__(action.label));
		const button = frm.add_custom_button(__(action.label), () =>
			run_next_action(frm, summary)
		);
		button.addClass("btn-primary");
	}

	if (frm.doc.docstatus === 1 && !summary.pick_list && !summary.delivery_notes.total) {
		frm.add_custom_button(
			__("Create Pick List (Optional)"),
			() => {
				frm.call("create_pick_list").then((response) => {
					if (response.message) frappe.set_route("Form", "Pick List", response.message);
				});
			},
			__("Warehouse")
		);
	}
	if (
		frm.doc.docstatus === 1 &&
		summary.vehicle_allocation?.docstatus === 1 &&
		!["Completed", "Completed with Exceptions", "Cancelled"].includes(frm.doc.status)
	) {
		frm.add_custom_button(
			__("Reallocate Vehicle"),
			() => {
				frappe.model.open_mapped_doc({
					method: "vuna_transport.services.fleet.vehicle_allocation_service.make_replacement_allocation_from_load",
					frm,
					freeze_message: __("Preparing replacement allocation..."),
				});
			},
			__("Dispatch")
		);
	}
}

function run_next_action(frm, summary) {
	switch (summary.next_action.key) {
		case "create_vehicle_allocation":
			return frappe.model.open_mapped_doc({
				method: "vuna_transport.services.fleet.vehicle_allocation_service.make_vehicle_allocation_from_load",
				frm,
				freeze_message: __("Preparing Vehicle Allocation..."),
			});
		case "open_vehicle_allocation":
			return frappe.set_route("Form", "Vehicle Allocation", summary.vehicle_allocation.name);
		case "create_pick_list":
			return frm.call("create_pick_list").then((response) => {
				if (response.message) frappe.set_route("Form", "Pick List", response.message);
			});
		case "open_pick_list":
			return frappe.set_route("Form", "Pick List", summary.pick_list.name);
		case "create_delivery_notes":
			return frm.call("create_delivery_notes").then((response) => {
				open_delivery_notes(response.message || []);
			});
		case "review_delivery_notes":
			return open_delivery_notes(summary.delivery_notes.names);
		case "create_delivery_trip":
			return frappe.model.open_mapped_doc({
				method: "vuna_transport.services.integrations.erpnext_integration.make_delivery_trip_from_load",
				frm,
				freeze_message: __("Preparing Delivery Trip..."),
			});
		case "open_delivery_trip":
			return frappe.set_route("Form", "Delivery Trip", summary.delivery_trip.name);
	}
}

function open_delivery_notes(names) {
	if (names.length === 1) return frappe.set_route("Form", "Delivery Note", names[0]);
	if (names.length) return frappe.set_route("List", "Delivery Note", { name: ["in", names] });
}

function render_new_load_progress(frm) {
	frm.get_field("execution_overview")?.$wrapper.html(
		`<div class="text-muted">${__(
			"Add Delivery Orders, review capacity, save and submit the load to begin execution."
		)}</div>`
	);
}

function render_execution_progress(frm, summary) {
	const load_step = {
		label: __("Load"),
		value: summary.load.docstatus === 1 ? __("Submitted") : __("Draft"),
		state: summary.load.docstatus === 1 ? "complete" : "current",
	};
	const allocation_step = {
		label: __("Vehicle Allocation"),
		value: !summary.workflow.requires_vehicle_allocation
			? __("Not Required")
			: summary.vehicle_allocation
			? __(summary.vehicle_allocation.document_status)
			: __("Not Allocated"),
		state: !summary.workflow.requires_vehicle_allocation
			? "complete"
			: summary.vehicle_allocation?.docstatus === 1
			? "complete"
			: summary.vehicle_allocation
			? "current"
			: "pending",
	};
	const picking_step = {
		label: __("Picking"),
		value: summary.pick_list ? __(summary.pick_list.document_status) : __("Optional"),
		state:
			summary.pick_list?.docstatus === 1
				? "complete"
				: summary.pick_list
				? "current"
				: "pending",
	};
	const delivery_notes_step = {
		label: __("Delivery Notes"),
		value: __("{0} of {1} submitted", [
			summary.delivery_notes.submitted,
			summary.delivery_notes.expected,
		]),
		state:
			summary.delivery_notes.expected &&
			summary.delivery_notes.submitted >= summary.delivery_notes.expected
				? "complete"
				: summary.delivery_notes.total
				? "current"
				: "pending",
	};
	const trip_step = {
		label: __("Delivery Trip"),
		value: !summary.workflow.requires_delivery_trip
			? __("Not Required")
			: summary.delivery_trip
			? __(summary.delivery_trip.document_status)
			: __("Not Created"),
		state: !summary.workflow.requires_delivery_trip
			? "complete"
			: summary.delivery_trip?.docstatus === 1
			? "complete"
			: summary.delivery_trip
			? "current"
			: "pending",
	};
	const pod_step = {
		label: __("Proof of Delivery"),
		value: !summary.workflow.requires_delivery_trip
			? __("Not Required")
			: __("{0} of {1} submitted", [
					summary.proofs_of_delivery.submitted,
					summary.stops.total,
			  ]),
		state: !summary.workflow.requires_delivery_trip
			? "complete"
			: summary.stops.total && summary.proofs_of_delivery.submitted >= summary.stops.total
			? "complete"
			: summary.proofs_of_delivery.total
			? "current"
			: "pending",
	};
	const return_step = {
		label: __("Return Requests"),
		value: summary.return_requests?.total
			? __("{0} follow-up", [summary.return_requests.total])
			: __("None"),
		state: "complete",
	};
	const allocation_after_notes =
		summary.workflow.vehicle_allocation_timing === "After Delivery Notes";
	const operational_steps = allocation_after_notes
		? [picking_step, delivery_notes_step, allocation_step]
		: [allocation_step, picking_step, delivery_notes_step];
	const steps = [load_step, ...operational_steps, trip_step, pod_step, return_step];

	const colors = { complete: "green", current: "blue", pending: "gray" };
	const cards = steps
		.map(
			(step, index) => `
				<div style="min-width:0; min-height:82px; padding:8px; border:1px solid var(--border-color); border-radius:6px; background:var(--card-bg);">
					<div class="text-muted small">${index + 1}. ${frappe.utils.escape_html(step.label)}</div>
					<div style="font-weight:600; margin-top:3px;">${frappe.utils.escape_html(step.value)}</div>
					<div class="indicator-pill ${colors[step.state]}" style="margin-top:5px;">${__(step.state)}</div>
				</div>`
		)
		.join("");

	frm.get_field("execution_overview").$wrapper.html(`
		<div style="display:flex; align-items:center; justify-content:space-between; gap:10px; padding:8px 10px; border:1px solid var(--border-color); border-radius:6px; background:var(--control-bg);">
			<span><span class="indicator-pill ${colors[load_step.state]}">${frappe.utils.escape_html(
		load_step.value
	)}</span><span class="text-muted small" style="margin-left:8px;">${frappe.utils.escape_html(
		__(summary.next_action.label)
	)}</span></span>
			<details style="display:inline-block;"><summary class="text-muted small" style="cursor:pointer;">${__(
				"View progress"
			)}</summary>
				<div style="position:absolute; z-index:5; right:var(--padding-md); width:min(720px, calc(100vw - 40px)); padding:10px; border:1px solid var(--border-color); border-radius:6px; background:var(--card-bg); box-shadow:var(--card-shadow);">
					<div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:6px;">${cards}</div>
					<div class="small text-muted" style="margin-top:8px;">${frappe.utils.escape_html(
						__(summary.next_action.description)
					)}</div>
				</div>
			</details>
		</div>
	`);
}

frappe.ui.form.on("Transport Load Item", {
	delivery_orders_add(frm) {
		recalculate_load_totals(frm);
	},

	delivery_orders_remove(frm) {
		recalculate_load_totals(frm);
	},

	delivery_order(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.delivery_order) return;

		frappe.db.get_doc("Delivery Order", row.delivery_order).then((delivery_order) => {
			frappe.model.set_value(cdt, cdn, "sales_order", delivery_order.sales_order);
			frappe.model.set_value(cdt, cdn, "customer", delivery_order.customer);
			frappe.model.set_value(cdt, cdn, "delivery_address", delivery_order.delivery_address);
			frappe.model.set_value(cdt, cdn, "delivery_group", delivery_order.delivery_group);
			frappe.model.set_value(
				cdt,
				cdn,
				"transport_compatibility_group",
				delivery_order.transport_compatibility_group
			);
		});
	},

	delivery_order_item(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.delivery_order_item) {
			frappe.db.get_doc("Delivery Order Item", row.delivery_order_item).then((item) => {
				frappe.model.set_value(cdt, cdn, "item_code", item.item_code);
				frappe.model.set_value(cdt, cdn, "item_name", item.item_name);
				frappe.model.set_value(cdt, cdn, "uom", item.uom);
				frappe.model.set_value(cdt, cdn, "stock_uom", item.stock_uom);
				frappe.model.set_value(cdt, cdn, "delivery_group", item.delivery_group);
				frappe.model.set_value(
					cdt,
					cdn,
					"transport_compatibility_group",
					item.transport_compatibility_group
				);
			});
		}
		update_allocation_preview(frm, cdt, cdn, true);
	},

	allocated_quantity(frm, cdt, cdn) {
		update_allocation_preview(frm, cdt, cdn, false);
	},
});

function update_allocation_preview(frm, cdt, cdn, use_available_quantity) {
	const row = locals[cdt][cdn];
	if (!row.delivery_order_item) return;
	const requested_quantity =
		use_available_quantity || flt(row.allocated_quantity) <= 0
			? null
			: flt(row.allocated_quantity);

	frappe
		.call({
			method: "vuna_transport.services.planning.delivery_load_service.get_delivery_order_item_allocation_preview",
			args: {
				delivery_order_item: row.delivery_order_item,
				allocated_quantity: requested_quantity,
				exclude_load: frm.is_new() ? null : frm.doc.name,
				source_warehouse: frm.doc.source_warehouse,
			},
		})
		.then(({ message }) => {
			if (!message || locals[cdt][cdn].delivery_order_item !== row.delivery_order_item)
				return;
			[
				"delivery_order",
				"sales_order",
				"customer",
				"delivery_address",
				"item_code",
				"item_name",
				"uom",
				"stock_uom",
				"available_quantity",
				"allocated_quantity",
				"stock_available_quantity",
				"stock_required_quantity",
				"stock_availability_status",
				"weight",
				"weight_uom",
				"volume",
				"volume_uom",
				"pallets",
				"cases",
				"crates",
			].forEach((fieldname) => {
				frappe.model.set_value(cdt, cdn, fieldname, message[fieldname]);
			});
			recalculate_load_totals(frm);
		});
}

function recalculate_load_totals(frm) {
	const rows = frm.doc.delivery_orders || [];
	const total = (fieldname) => rows.reduce((sum, row) => sum + flt(row[fieldname]), 0);
	const values = {
		total_weight: total("weight"),
		total_volume: total("volume"),
		total_pallets: total("pallets"),
		total_cases: total("cases"),
		total_crates: total("crates"),
		total_stops: new Set(rows.map((row) => row.delivery_address).filter(Boolean)).size,
	};
	values.minimum_payload = values.total_weight;
	values.minimum_volume = values.total_volume;

	Object.entries(values).forEach(([fieldname, value]) => {
		frm.doc[fieldname] = value;
	});
	frm.refresh_fields(Object.keys(values));
	render_capacity_summary(frm);
}

function show_item_selection_dialog(frm, result) {
	const existing = new Set(
		(frm.doc.delivery_orders || []).map(
			(row) => `${row.delivery_order}::${row.delivery_order_item}`
		)
	);
	const items = (result.items || []).filter(
		(row) => !existing.has(`${row.delivery_order}::${row.delivery_order_item}`)
	);
	if (!items.length) {
		frappe.msgprint(__("The selected Delivery Orders have no new unallocated items."));
		return;
	}
	const dialog = new frappe.ui.Dialog({
		title: __("Select Items to Load"),
		size: "extra-large",
		fields: [{ fieldtype: "HTML", fieldname: "items" }],
		primary_action_label: __("Add Selected Items"),
		primary_action() {
			const selected = new Set(
				dialog.$wrapper
					.find(".vuna-load-item:checked")
					.map((_, input) => $(input).data("item"))
					.get()
			);
			if (!selected.size) {
				frappe.msgprint(__("Select at least one item."));
				return;
			}
			[
				"company",
				"source_warehouse",
				"delivery_method",
				"transport_zone",
				"transport_route",
				"dispatch_date",
				"weight_uom",
				"volume_uom",
			].forEach((field) => {
				if (!frm.doc[field] && result[field]) frm.doc[field] = result[field];
			});
			items
				.filter((row) => selected.has(row.delivery_order_item))
				.forEach((row) => frm.add_child("delivery_orders", row));
			recalculate_load_totals(frm);
			frm.refresh_fields();
			frm.dirty();
			dialog.hide();
			frappe.show_alert({
				message: __("Selected items added to the Transport Load"),
				indicator: "green",
			});
		},
	});
	const number = (value) => frappe.format(value || 0, { fieldtype: "Float" });
	const rows = items
		.map((row) => {
			const sufficient = row.stock_availability_status === "Available";
			return `<tr><td><input class="vuna-load-item" type="checkbox" checked data-item="${frappe.utils.escape_html(
				row.delivery_order_item
			)}"></td><td>${frappe.utils.escape_html(
				row.delivery_order
			)}</td><td><strong>${frappe.utils.escape_html(
				row.item_code
			)}</strong><br><span class="text-muted small">${frappe.utils.escape_html(
				row.item_name || ""
			)}</span></td><td>${number(row.available_quantity)} ${frappe.utils.escape_html(
				row.uom || ""
			)}</td><td><span class="indicator-pill ${sufficient ? "green" : "orange"}">${number(
				row.stock_available_quantity
			)} ${frappe.utils.escape_html(
				row.stock_uom || ""
			)}</span><br><span class="text-muted small">${
				sufficient ? __("On hand") : __("Insufficient on hand")
			}</span></td></tr>`;
		})
		.join("");
	dialog
		.get_field("items")
		.$wrapper.html(
			`<p class="text-muted small">${__(
				"On-hand stock is a planning warning. Pick List and Delivery Note remain the stock authority."
			)}</p><table class="table table-bordered"><thead><tr><th></th><th>${__(
				"Delivery Order"
			)}</th><th>${__("Item")}</th><th>${__("Transport Available")}</th><th>${__(
				"On Hand"
			)}</th></tr></thead><tbody>${rows}</tbody></table>`
		);
	dialog.show();
}

function render_capacity_summary(frm) {
	const field = frm.get_field("capacity_summary");
	if (!field) return;
	if (!frm.doc.required_vehicle_category) {
		field.$wrapper.html(
			`<div class="text-muted">${__(
				"Select a Vehicle Category to view occupied and remaining capacity."
			)}</div>`
		);
		return;
	}
	field.$wrapper.html(`<div class="text-muted">${__("Calculating capacity...")}</div>`);
	frappe
		.call({
			method: "vuna_transport.services.planning.delivery_load_service.get_vehicle_category_capacity_summary",
			args: {
				vehicle_category: frm.doc.required_vehicle_category,
				totals: {
					weight: frm.doc.total_weight || 0,
					volume: frm.doc.total_volume || 0,
					pallets: frm.doc.total_pallets || 0,
					cases: frm.doc.total_cases || 0,
					crates: frm.doc.total_crates || 0,
				},
				weight_uom: frm.doc.weight_uom,
				volume_uom: frm.doc.volume_uom,
			},
		})
		.then(({ message }) => {
			if (!message || frm.doc.required_vehicle_category !== message.vehicle_category) return;
			field.$wrapper.html(render_capacity_breakdown(message));
		})
		.catch(() => {
			field.$wrapper.html(
				`<div class="text-muted">${__("Capacity summary could not be loaded.")}</div>`
			);
		});
}

function render_capacity_breakdown(result) {
	const labels = {
		weight: __("Weight"),
		volume: __("Volume"),
	};
	const number = (value) => frappe.format(value || 0, { fieldtype: "Float" });
	const status_class = (utilization) => {
		if (utilization >= 100) return "red";
		if (utilization >= 70) return "green";
		return "blue";
	};
	const rows = Object.entries(result.capacity || {})
		.filter(([dimension]) => ["weight", "volume"].includes(dimension))
		.filter(([, values]) => values.capacity)
		.map(
			([dimension, values]) =>
				`<tr><td class="text-muted">${labels[dimension]}</td><td>${number(
					values.used
				)}</td><td>${number(values.capacity)}</td><td class="${status_class(
					values.utilization
				)}">${number(values.remaining)}</td><td><span class="indicator-pill ${status_class(
					values.utilization
				)}">${number(values.utilization)}%</span></td></tr>`
		)
		.join("");
	return `<div class="vuna-capacity-summary"><table class="table table-bordered table-condensed" style="margin:0;"><thead><tr><th>${__(
		"Dimension"
	)}</th><th>${__("Occupied")}</th><th>${__("Capacity")}</th><th>${__(
		"Available"
	)}</th><th style="width:110px;">${__(
		"Used"
	)}</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}
