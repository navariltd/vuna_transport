/* global erpnext */

function is_depot_only_stop(frm, stop) {
	return (
		stop.vuna_stop_type === "Depot" ||
		(frm.doc.vuna_task_based &&
			stop.vuna_task_keys &&
			!stop.delivery_note &&
			stop.vuna_stop_type !== "Mixed")
	);
}

frappe.ui.form.on("Delivery Stop", {
	form_render(frm, cdt, cdn) {
		const stop = frappe.get_doc(cdt, cdn);
		const row = frm.fields_dict.delivery_stops.grid.grid_rows_by_docname[cdn];
		if (row) row.toggle_editable("customer", !is_depot_only_stop(frm, stop));
	},
	async vuna_record_collection(frm, cdt, cdn) {
		if (
			frm.doc.docstatus !== 1 ||
			!frm.doc.vuna_actual_start ||
			frm.doc.vuna_actual_completion ||
			frm.doc.status === "Completed"
		) {
			frappe.msgprint(__("Collections require a started, active trip."));
			return;
		}
		const { message } = await frappe.call({
			method: "vuna_transport.services.execution.unplanned_pickup_policy.get_unplanned_pickup_policy",
		});
		if (!message.enabled) {
			frappe.msgprint(
				__(
					"Unplanned return pickups are disabled. Use an approved Return Request pickup task."
				)
			);
			return;
		}
		const stop = frappe.get_doc(cdt, cdn);
		frappe.new_doc("Proof of Delivery", {
			proof_type: "Unplanned Collection",
			company: frm.doc.company,
			delivery_trip: frm.doc.name,
			collection_stop: stop.name,
			direction: "Pickup",
			collection_party_type: stop.customer ? "Customer" : "Depot",
			collection_location: [stop.customer, stop.address].filter(Boolean).join(" — "),
			delivery_datetime: frappe.datetime.now_datetime(),
		});
	},
});

frappe.ui.form.on("Delivery Trip", {
	setup(frm) {
		frm.set_query("vehicle", () => {
			const loads = (frm.doc.delivery_stops || [])
				.map((row) => row.vuna_delivery_load)
				.filter(Boolean);
			if (!loads.length) return {};
			return {
				query: "vuna_transport.api.get_vehicles_for_load",
				filters: { delivery_load: loads[0] },
			};
		});
	},

	refresh(frm) {
		if (frm.perm?.some((p) => p.write) && !frm.is_new() && frm.doc.docstatus !== 2)
			frappe
				.call({
					method: "vuna_transport.services.routing.routing_policy_service.get_routing_context",
				})
				.then(({ message }) => {
					if (!message?.can_preview) return;
					frm.add_custom_button(
						__("Road Route Preview"),
						() => {
							frappe
								.require("/assets/vuna_transport/js/road_routing.js")
								.then(() =>
									window.vuna_transport.road_routing.show_preview(frm, message)
								);
						},
						__("Actions")
					);
				});
		const grid = frm.fields_dict?.delivery_stops?.grid;
		if (grid) {
			const stops = frm.doc.delivery_stops || [];
			grid.update_docfield_property(
				"customer",
				"hidden",
				stops.length > 0 && stops.every((stop) => is_depot_only_stop(frm, stop))
			);
			grid.update_docfield_property("vuna_record_collection", "hidden", true);
			if (
				frm.doc.docstatus === 1 &&
				frm.doc.vuna_actual_start &&
				!frm.doc.vuna_actual_completion &&
				frm.doc.status !== "Completed"
			) {
				frappe.call({
					method: "vuna_transport.services.execution.unplanned_pickup_policy.get_unplanned_pickup_policy",
					callback: ({ message }) =>
						grid.update_docfield_property(
							"vuna_record_collection",
							"hidden",
							!message.enabled
						),
				});
			}
		}
		// ERPNext resets this list in refresh, so setup is too early.
		// Stop upstream traversal, but retain POD and its downstream cascade.
		frm.ignore_doctypes_on_cancel_all = [
			...new Set([
				...(frm.ignore_doctypes_on_cancel_all || []),
				"Vehicle Allocation",
				"Transport Load",
				"Transport Trip Task",
				"Delivery Note",
				// ERPNext cancels bundles with their stock voucher, not before it.
				"Serial and Batch Bundle",
			]),
		];
		frm.toggle_display("optimize_route", !frm.doc.vuna_task_based);
		const loads = (frm.doc.delivery_stops || [])
			.map((row) => row.vuna_delivery_load)
			.filter(Boolean);

		if (frm.doc.docstatus === 0 && !frm.doc.vuna_task_based) {
			frm.remove_custom_button(__("Delivery Note"), __("Get stops from"));
			frm.add_custom_button(
				__("Delivery Note"),
				() => {
					const get_query_filters = {
						company: frm.doc.company,
						docstatus: 1,
						status: ["!=", "Closed"],
					};
					if (loads.length) get_query_filters.vuna_delivery_load = ["in", loads];

					erpnext.utils.map_current_doc({
						method: "erpnext.stock.doctype.delivery_note.delivery_note.make_delivery_trip",
						source_doctype: "Delivery Note",
						target: frm,
						date_field: "posting_date",
						setters: {
							company: frm.doc.company,
							customer: null,
						},
						get_query_filters,
					});
				},
				__("Get stops from")
			);
		}
		if (frm.doc.vuna_task_based)
			frm.remove_custom_button(__("Delivery Note"), __("Get stops from"));
		if (
			loads.length &&
			frm.doc.docstatus < 2 &&
			!["Completed", "Cancelled"].includes(frm.doc.status) &&
			(frm.doc.vuna_current_vehicle_allocation || frm.doc.vuna_vehicle_allocation)
		) {
			frm.add_custom_button(
				__("Reallocate Vehicle"),
				() => {
					frappe.model.open_mapped_doc({
						method: "vuna_transport.services.fleet.vehicle_allocation_service.make_replacement_allocation_from_trip",
						frm,
						freeze_message: __("Preparing replacement allocation..."),
					});
				},
				__("Create")
			);
		}

		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(
				__("Create Gate Pass"),
				() => {
					frappe.route_options = {
						company: frm.doc.company,
						delivery_trip: frm.doc.name,
						direction: "Outbound",
					};
					frappe.new_doc("Gate Pass");
				},
				__("Create")
			);
			const pending_stops = (frm.doc.delivery_stops || []).filter(
				(stop) => stop.delivery_note && !stop.vuna_proof_of_delivery
			);
			if (pending_stops.length) {
				frm.add_custom_button(
					__("Create Proof of Delivery"),
					() => {
						const dialog = new frappe.ui.Dialog({
							title: __("Select Delivery Stop"),
							fields: [
								{
									fieldname: "delivery_stop",
									fieldtype: "Select",
									label: __("Delivery Stop"),
									options: pending_stops.map((stop, index) => ({
										label: __("Stop {0}: {1}", [index + 1, stop.customer]),
										value: stop.name,
									})),
									reqd: 1,
								},
							],
							primary_action_label: __("Create"),
							primary_action(values) {
								dialog.disable_primary_action();
								frappe
									.call({
										method: "vuna_transport.services.execution.pod_service.create_proof_of_delivery",
										args: {
											delivery_trip: frm.doc.name,
											delivery_stop: values.delivery_stop,
										},
									})
									.then((response) => {
										if (response.message) {
											frappe.set_route(
												"Form",
												"Proof of Delivery",
												response.message
											);
										}
									});
							},
						});
						dialog.show();
					},
					__("Create")
				);
			}
		}
		frm.page.set_inner_btn_group_as_primary(__("Create"));
	},
});
