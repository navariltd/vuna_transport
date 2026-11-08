frappe.ui.form.on("Stock Entry", {
	async refresh(frm) {
		if (
			frm.doc.docstatus === 1 &&
			!frm.doc.add_to_transit &&
			(frm.doc.outgoing_stock_entry || frm.doc.vuna_return_request)
		) {
			const source = frm.doc.name;
			const { message: requests } = await frappe.call({
				method: "vuna_transport.services.execution.depot_return_inspection_service.get_receipt_requests",
				args: { receiving_stock_entry: source },
			});
			if (frm.doc.name === source && requests?.length)
				frm.add_custom_button(
					__("Return Inspection"),
					() => {
						const create = (request) =>
							frappe.call({
								method: "vuna_transport.services.execution.depot_return_inspection_service.create_inspection",
								args: { receiving_stock_entry: source, return_request: request },
								freeze: true,
								callback: ({ message }) => {
									if (message)
										frappe.set_route("Form", "Return Inspection", message);
								},
							});
						if (requests.length === 1) create(requests[0]);
						else
							frappe.prompt(
								{
									fieldname: "request",
									fieldtype: "Select",
									options: requests,
									reqd: 1,
									label: __("Return Request"),
								},
								({ request }) => create(request)
							);
					},
					__("Create")
				);
		}
		if (!frm.doc.vuna_return_pickup_proof) return;
		// The pickup POD is this voucher's authority and has a navigation
		// backlink to it. Cancel receiving first, then stock, then pickup POD.
		// Server guards still block active receiving proofs and stock vouchers.
		frm.ignore_doctypes_on_cancel_all = [
			...new Set([
				...(frm.ignore_doctypes_on_cancel_all || []),
				"Proof of Delivery",
				"Transport Trip Task",
			]),
		];
	},
});
