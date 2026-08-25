frappe.ui.form.on("Delivery Trip", {
	setup(frm) {
		frm.set_query("vehicle", () => {
			if (!frm.doc.vuna_delivery_load) return {};
			return {
				query: "vuna_transport.api.get_vehicles_for_load",
				filters: { delivery_load: frm.doc.vuna_delivery_load },
			};
		});
	},

	refresh(frm) {
		if (frm.doc.vuna_delivery_load) {
			frm.add_custom_button(__("Open Delivery Load"), () => {
				frappe.set_route("Form", "Delivery Load", frm.doc.vuna_delivery_load);
			}, __("Vuna Transport"));
			frm.add_custom_button(__("Allocation History"), () => {
				frappe.set_route("List", "Vehicle Allocation", {
					delivery_load: frm.doc.vuna_delivery_load,
				});
			}, __("Vuna Transport"));
		}

		if (
			frm.doc.vuna_delivery_load &&
			frm.doc.docstatus < 2 &&
			!["Completed", "Cancelled"].includes(frm.doc.status) &&
			(frm.doc.vuna_current_vehicle_allocation || frm.doc.vuna_vehicle_allocation)
		) {
			frm.add_custom_button(__("Reallocate Vehicle"), () => {
				frappe.model.open_mapped_doc({
					method: "vuna_transport.services.fleet.vehicle_allocation_service.make_replacement_allocation_from_trip",
					frm,
					freeze_message: __("Preparing replacement allocation..."),
				});
			}, __("Vuna Transport"));
		}

		if (frm.doc.docstatus === 1) {
			const pending_stops = (frm.doc.delivery_stops || []).filter(
				(stop) => stop.delivery_note && !stop.vuna_proof_of_delivery,
			);
			if (pending_stops.length) {
				frm.add_custom_button(__("Create Proof of Delivery"), () => {
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
							frappe.call({
								method: "vuna_transport.services.execution.pod_service.create_proof_of_delivery",
								args: {
									delivery_trip: frm.doc.name,
									delivery_stop: values.delivery_stop,
								},
							}).then((response) => {
								if (response.message) {
									frappe.set_route("Form", "Proof of Delivery", response.message);
								}
							});
						},
					});
					dialog.show();
				}, __("Vuna Transport"));
			}
		}
	},
});
