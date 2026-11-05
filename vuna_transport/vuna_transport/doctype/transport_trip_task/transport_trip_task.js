frappe.ui.form.on("Transport Trip Task", {
	refresh(frm) {
		if (frm.doc.return_request && frm.doc.status !== "Cancelled") {
			frm.add_custom_button(
				__("Remove Pickup Assignment"),
				() =>
					frappe.confirm(
						__(
							"Remove this direct pickup assignment? Any pickup POD and stock must be cancelled first."
						),
						() =>
							frappe.call({
								method: "vuna_transport.services.execution.direct_return_pickup_service.remove_return_pickup",
								args: { task_name: frm.doc.name },
								freeze: true,
								callback: () => frm.reload_doc(),
							})
					),
				__("Actions")
			);
		}
		if (
			["Transfer Receipt", "Return Pickup"].includes(frm.doc.task_type) &&
			frm.doc.status !== "Cancelled"
		) {
			frm.add_custom_button(
				__(
					frm.doc.task_type === "Return Pickup" ? "Proof of Pickup" : "Proof of Delivery"
				),
				() => {
					frappe.model.open_mapped_doc({
						method: "vuna_transport.services.execution.transfer_proof_service.make_transfer_proof",
						frm,
					});
				},
				__("Create")
			);
		}
	},
});
