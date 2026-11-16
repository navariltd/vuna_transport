frappe.ui.form.on("Shipment Diversion", {
	setup(frm) {
		frm.fields_dict.items.grid.cannot_add_rows = true;
		frm.set_query("customer", () => ({
			filters: { disabled: 0, name: ["!=", frm.doc.original_customer] },
		}));
		frm.set_query("delivery_address", () =>
			!frm.doc.customer
				? { filters: { name: ["=", ""] } }
				: {
						query: "frappe.contacts.doctype.address.address.address_query",
						filters: { link_doctype: "Customer", link_name: frm.doc.customer },
				  }
		);
		frm.set_query("destination_warehouse", () => ({
			filters: {
				company: frm.doc.company,
				is_group: 0,
				disabled: 0,
				warehouse_type: ["!=", "Transit"],
			},
		}));
	},
	refresh(frm) {
		if (
			frm.doc.name &&
			!frm.doc.__islocal &&
			frm.doc.docstatus !== 2 &&
			frm.perm?.some((p) => p.write)
		) {
			const execute = (method) => {
				if (frm.is_dirty()) return frappe.msgprint(__("Save your changes first."));
				frappe.call({
					method: `vuna_transport.services.execution.shipment_diversion_execution.${method}`,
					args: { shipment_diversion: frm.doc.name },
					freeze: true,
					callback: () => frm.reload_doc(),
				});
			};
			if (!frm.doc.execution_task && !["Completed", "Needs Review"].includes(frm.doc.status))
				frm.add_custom_button(
					__("Resume Original Trip"),
					() =>
						frappe.confirm(
							__(
								"Resume the original trip for these goods? Vehicle and driver availability will be checked."
							),
							() => execute("resume_original_trip")
						),
					__("Actions")
				);
			if (frm.doc.docstatus === 1) {
				if (
					frm.doc.destination_type === "Depot" &&
					frm.doc.status === "Needs Review" &&
					frappe.model.can_create("Shipment Diversion")
				)
					frm.add_custom_button(
						__("Redirect Remaining Goods"),
						() => {
							if (frm.is_dirty())
								return frappe.msgprint(__("Save your changes first."));
							frappe.model.open_mapped_doc({
								method: "vuna_transport.services.execution.shipment_diversion_followup.make_followup_diversion",
								frm,
							});
						},
						__("Actions")
					);
				if (!frm.doc.execution_task)
					frm.add_custom_button(
						__("Add to Original Trip"),
						() => execute("add_diversion_to_trip"),
						__("Actions")
					);
				else
					frm.add_custom_button(
						__("Remove Trip Assignment"),
						() =>
							frappe.confirm(
								__(
									"Remove this assignment? Its stop history will be kept on the cancelled task. Stock and sales documents will not be reversed."
								),
								() => execute("remove_diversion_assignment")
							),
						__("Actions")
					);
			}
		}
		if (!frm.doc.return_request)
			return frm.set_intro(
				__("Open a submitted Return Request and choose Create → Divert Shipment."),
				"blue"
			);
		if (frm.doc.docstatus !== 2)
			frm.set_intro(
				__(
					frm.doc.previous_diversion
						? "This follow-up reuses the posted vehicle receipt. Do not create another Sales Return; customer redelivery needs a fresh accepted Quality Inspection."
						: "Stock documents and trip changes are not created by approval. Create and submit the Sales Return first; customer redelivery also needs accepted Quality Inspections."
				),
				"blue"
			);
		if (frm.doc.docstatus === 1 && frm.perm?.some((permission) => permission.write)) {
			const actions = frm.doc.previous_diversion ? [] : [["Sales Return", "Delivery Note"]];
			if (frm.doc.destination_type === "Customer")
				actions.push(["Sales Order", "Sales Order"], ["Delivery Note", "Delivery Note"]);
			else actions.push(["Stock Entry", "Stock Entry"]);
			for (const [label, doctype] of actions) {
				if (!frappe.model.can_create(doctype)) continue;
				frm.add_custom_button(
					__(label),
					() => {
						if (frm.is_dirty()) return frappe.msgprint(__("Save your changes first."));
						frappe.call({
							method: "vuna_transport.services.execution.shipment_diversion_documents.create_diversion_document",
							args: { shipment_diversion: frm.doc.name, document_type: label },
							freeze: true,
							callback(response) {
								if (!response.message) return;
								frm.reload_doc();
								frappe.set_route(
									"Form",
									response.message.doctype,
									response.message.name
								);
							},
						});
					},
					__("Create")
				);
			}
		}
	},
	destination_type(frm) {
		if (frm.doc.destination_type === "Depot")
			frm.set_value({ customer: "", delivery_address: "" });
		else frm.set_value("destination_warehouse", "");
	},
	customer(frm) {
		frm.set_value("delivery_address", "");
	},
});
