frappe.ui.form.on("Delivery Note", {
	setup(frm) {
		frm.set_query("vuna_return_request", () => ({
			filters: {
				docstatus: 1,
				company: frm.doc.company,
				customer: frm.doc.customer,
				status: ["not in", ["Closed", "Cancelled"]],
			},
		}));
	},

	refresh(frm) {
		hide_standard_delivery_trip_action_for_allocated_load(frm);
		if (frm.doc.is_return) {
			configure_return_quality_inspection(frm);
			add_return_inspection_action(frm);
			return;
		}
		if (frm.doc.docstatus !== 1 || !frappe.model.can_create("Return Request")) {
			return;
		}
		frm.add_custom_button(
			__("Return Request"),
			() => {
				frappe.call({
					method: "vuna_transport.services.execution.return_service.create_return_request_from_delivery_note",
					args: { delivery_note: frm.doc.name },
					freeze: true,
					callback: ({ message }) => {
						if (message) frappe.set_route("Form", "Return Request", message);
					},
				});
			},
			__("Create")
		);
	},
});

function add_return_inspection_action(frm) {
	if (
		frm.is_new() ||
		frm.doc.docstatus !== 1 ||
		!frm.doc.vuna_return_request ||
		!frappe.model.can_create("Return Inspection")
	)
		return;

	frm.add_custom_button(
		__("Return Inspection"),
		() =>
			frappe.call({
				method: "vuna_transport.services.execution.return_v2_service.create_return_inspection_from_sales_return",
				args: { sales_return: frm.doc.name },
				freeze: true,
				callback: ({ message }) => {
					if (message) frappe.set_route("Form", "Return Inspection", message);
				},
			}),
		__("Create")
	);
	frm.page.set_inner_btn_group_as_primary(__("Create"));
}

function hide_standard_delivery_trip_action_for_allocated_load(frm) {
	if (frm.doc.docstatus !== 1 || !frm.doc.vuna_delivery_load) return;
	const delivery_note = frm.doc.name;

	frappe.db
		.get_value("Delivery Load", frm.doc.vuna_delivery_load, "active_vehicle_allocation")
		.then(({ message }) => {
			if (!message?.active_vehicle_allocation || frm.doc.name !== delivery_note) return;
			// ERPNext's Delivery Note controller can add its buttons after custom
			// form refresh handlers. Defer removal until that standard refresh work
			// has completed, while retaining the normal path for standalone notes.
			setTimeout(() => {
				if (frm.doc.name === delivery_note) {
					frm.remove_custom_button(__("Delivery Trip"), __("Create"));
				}
			}, 250);
		});
}

function configure_return_quality_inspection(frm) {
	if (frm.is_new()) return;
	frappe.db
		.get_value("Return Request", { return_stock_document: frm.doc.name, docstatus: 1 }, [
			"name",
			"inspection_required",
		])
		.then(({ message }) => {
			if (!message?.name) return;
			if (!frm.doc.vuna_return_request) {
				frm.doc.vuna_return_request = message.name;
				frm.refresh_field("vuna_return_request");
			}
		});
}
