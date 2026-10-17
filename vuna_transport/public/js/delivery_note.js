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
		if (frm.doc.is_return) {
			configure_return_quality_inspection(frm);
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
			frm.add_custom_button(__("Open Return Request"), () => {
				frappe.set_route("Form", "Return Request", message.name);
			});
		});
}
