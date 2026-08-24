// Copyright (c) 2026, Yovuna Technologies Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("Delivery Load", {
	setup(frm) {
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
		frappe.db.get_value(
			"Transport Route",
			frm.doc.transport_route,
			["estimated_distance", "estimated_duration"],
		).then(({ message }) => {
			if (!message) return;
			if (!frm.doc.estimated_distance) frm.set_value("estimated_distance", message.estimated_distance);
			if (!frm.doc.estimated_duration) frm.set_value("estimated_duration", message.estimated_duration);
		});
	},

	refresh(frm) {
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Get Items"), () => {
				new frappe.ui.form.MultiSelectDialog({
					doctype: "Delivery Order",
					target: frm,
					size: "extra-large",
					add_filters_group: 1,
					columns: [
						"name",
						"customer_name",
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
					},
					get_query() {
						return {
							query: "vuna_transport.services.delivery_load_service.get_available_delivery_orders",
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
						frappe.call({
							method: "vuna_transport.services.delivery_load_service.get_delivery_order_items",
							args: { delivery_order_names: selections },
						}).then((response) => {
							const result = response.message;
							if (!result) return;

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

							const existing = new Set(
								(frm.doc.delivery_orders || []).map(
									(row) => `${row.delivery_order}::${row.delivery_order_item}`,
								)
							);
							result.items.forEach((row) => {
								const key = `${row.delivery_order}::${row.delivery_order_item}`;
								if (existing.has(key)) return;
								frm.add_child("delivery_orders", row);
								existing.add(key);
							});

								recalculate_load_totals(frm);
								frm.refresh_fields();
							frm.dirty();
							frappe.show_alert({ message: __("Items added to the Delivery Load"), indicator: "green" });
						});
						cur_dialog.hide();
					},
				});
			}, __("Planning"));
		}

		if (frm.is_new()) {
			render_new_load_progress(frm);
			return;
		}

		frm.trigger("load_execution_progress");
	},

	async load_execution_progress(frm) {
		try {
			const response = await frappe.call({
				method: "vuna_transport.services.execution_service.get_load_execution_summary",
				args: { load: frm.doc.name },
			});
			if (!response.message) return;
			frm.execution_summary = response.message;
			add_execution_actions(frm, response.message);
			render_execution_progress(frm, response.message);
		} catch (error) {
			console.error("Unable to load Delivery Load execution progress", error);
			frm.get_field("execution_overview")?.$wrapper.html(
				`<div class="alert alert-warning">${__("Execution progress could not be loaded. Reload the form or use the available action above.")}</div>`,
			);
		}
	},
});

function add_execution_actions(frm, summary) {
	const action = summary.next_action;
	if (frm.doc.docstatus === 1 && !["completed", "cancelled"].includes(action.key)) {
		frm.remove_custom_button(__(action.label));
		const button = frm.add_custom_button(__(action.label), () => run_next_action(frm, summary));
		button.addClass("btn-primary");
	}

	if (summary.pick_list?.name) {
		frm.add_custom_button(__("Pick List"), () => {
			frappe.set_route("Form", "Pick List", summary.pick_list.name);
		}, __("View"));
	}
	if (
		frm.doc.docstatus === 1 &&
		!summary.pick_list &&
		!summary.delivery_notes.total
	) {
		frm.add_custom_button(__("Create Pick List (Optional)"), () => {
			frm.call("create_pick_list").then((response) => {
				if (response.message) frappe.set_route("Form", "Pick List", response.message);
			});
		}, __("Warehouse"));
	}
	if (summary.vehicle_allocation?.name) {
		frm.add_custom_button(__("Vehicle Allocation"), () => {
			frappe.set_route("Form", "Vehicle Allocation", summary.vehicle_allocation.name);
		}, __("View"));
		frm.add_custom_button(__("Allocation History"), () => {
			frappe.set_route("List", "Vehicle Allocation", { delivery_load: frm.doc.name });
		}, __("View"));
	}
	if (
		frm.doc.docstatus === 1 &&
		summary.vehicle_allocation?.docstatus === 1 &&
		!["Completed", "Completed with Exceptions", "Cancelled"].includes(frm.doc.status)
	) {
		frm.add_custom_button(__("Reallocate Vehicle"), () => {
			frappe.model.open_mapped_doc({
				method: "vuna_transport.services.vehicle_allocation_service.make_replacement_allocation_from_load",
				frm,
				freeze_message: __("Preparing replacement allocation..."),
			});
		}, __("Dispatch"));
	}
	if (summary.delivery_notes.names.length) {
		frm.add_custom_button(__("Delivery Notes"), () => open_delivery_notes(summary.delivery_notes.names), __("View"));
	}
	if (summary.delivery_trip?.name) {
		frm.add_custom_button(__("Delivery Trip"), () => {
			frappe.set_route("Form", "Delivery Trip", summary.delivery_trip.name);
		}, __("View"));
	}
}

function run_next_action(frm, summary) {
	switch (summary.next_action.key) {
		case "create_vehicle_allocation":
			return frappe.model.open_mapped_doc({
				method: "vuna_transport.services.vehicle_allocation_service.make_vehicle_allocation_from_load",
				frm,
				freeze_message: __("Preparing Vehicle Allocation..."),
			});
		case "set_schedule":
			frm.set_df_property("planned_departure", "reqd", 1);
			frm.set_df_property("planned_arrival", "reqd", 1);
			frm.scroll_to_field("planned_departure");
			frappe.show_alert({
				message: __("Set Planned Departure and Planned Arrival, then save the load."),
				indicator: "orange",
			});
			return;
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
				method: "vuna_transport.services.erpnext_integration.make_delivery_trip_from_load",
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
		`<div class="text-muted">${__("Add Delivery Orders, review capacity, save and submit the load to begin execution.")}</div>`,
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
		state:
			!summary.workflow.requires_vehicle_allocation
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
		state: summary.pick_list?.docstatus === 1 ? "complete" : summary.pick_list ? "current" : "pending",
	};
	const delivery_notes_step = {
		label: __("Delivery Notes"),
		value: __("{0} of {1} submitted", [summary.delivery_notes.submitted, summary.delivery_notes.expected]),
		state:
			summary.delivery_notes.expected && summary.delivery_notes.submitted >= summary.delivery_notes.expected
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
			: __("{0} of {1} submitted", [summary.proofs_of_delivery.submitted, summary.stops.total]),
		state:
			!summary.workflow.requires_delivery_trip
				? "complete"
				: summary.stops.total && summary.proofs_of_delivery.submitted >= summary.stops.total
					? "complete"
					: summary.proofs_of_delivery.total
						? "current"
						: "pending",
	};
	const allocation_after_notes =
		summary.workflow.vehicle_allocation_timing === "After Delivery Notes";
	const operational_steps = allocation_after_notes
		? [picking_step, delivery_notes_step, allocation_step]
		: [allocation_step, picking_step, delivery_notes_step];
	const steps = [load_step, ...operational_steps, trip_step, pod_step];

	const colors = { complete: "green", current: "blue", pending: "gray" };
	const cards = steps
		.map(
			(step, index) => `
				<div style="flex:1; min-width:140px; padding:12px; border:1px solid var(--border-color); border-radius:8px; background:var(--card-bg);">
					<div class="text-muted small">${index + 1}. ${frappe.utils.escape_html(step.label)}</div>
					<div style="font-weight:600; margin-top:4px;">${frappe.utils.escape_html(step.value)}</div>
					<div class="indicator-pill ${colors[step.state]}" style="margin-top:8px;">${__(step.state)}</div>
				</div>`,
		)
		.join("");

	frm.get_field("execution_overview").$wrapper.html(`
		<div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:14px;">${cards}</div>
		<div class="alert alert-info" style="margin-bottom:0;">
			<div style="font-weight:600;">${frappe.utils.escape_html(__(summary.next_action.label))}</div>
			<div class="small">${frappe.utils.escape_html(__(summary.next_action.description))}</div>
		</div>
	`);
}

frappe.ui.form.on("Delivery Load Order", {
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
		});
	},

	delivery_order_item(frm, cdt, cdn) {
		update_allocation_preview(frm, cdt, cdn, true);
	},

	allocated_quantity(frm, cdt, cdn) {
		update_allocation_preview(frm, cdt, cdn, false);
	},
});

function update_allocation_preview(frm, cdt, cdn, use_available_quantity) {
	const row = locals[cdt][cdn];
	if (!row.delivery_order_item) return;
	const requested_quantity = use_available_quantity || flt(row.allocated_quantity) <= 0
		? null
		: flt(row.allocated_quantity);

	frappe.call({
		method: "vuna_transport.services.delivery_load_service.get_delivery_order_item_allocation_preview",
		args: {
			delivery_order_item: row.delivery_order_item,
			allocated_quantity: requested_quantity,
			exclude_load: frm.is_new() ? null : frm.doc.name,
		},
	}).then(({ message }) => {
		if (!message || locals[cdt][cdn].delivery_order_item !== row.delivery_order_item) return;
		[
			"delivery_order",
			"sales_order",
			"customer",
			"delivery_address",
			"available_quantity",
			"allocated_quantity",
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
}
