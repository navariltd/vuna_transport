/* global erpnext */

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
