frappe.ui.form.on("Transport Trip Task", {
	refresh(frm) {
		if (frm.doc.task_type === "Transfer Receipt" && frm.doc.status !== "Cancelled") {
			frm.add_custom_button(
				__("Proof of Delivery"),
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
