frappe.pages["dispatch-scan"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Dispatch Scan"),
		single_column: true,
	});

	page.set_secondary_action(
		__("Back to Delivery Notes"),
		() => {
			frappe.set_route("List", "Delivery Note");
		},
		"arrow-left"
	);

	const $content = $(wrapper).find(".layout-main-section");
	frappe
		.call({
			method: "vuna_transport.services.execution.delivery_note_scan_service.get_available_scan_actions",
		})
		.then((response) => {
			const actions = response.message || {};
			const availableActions = ["Loaded", "Dispatched"].filter((action) => actions[action]);
			if (!availableActions.length) {
				$content.html(
					`<div class="alert alert-danger">${__(
						"You do not have permission to scan Delivery Notes."
					)}</div>`
				);
				return;
			}

			$content.html(`
		<div class="container-fluid" style="max-width: 560px;">
			<div class="card p-4 text-center">
				<div class="btn-group btn-group-lg w-100 mb-4 scan-actions" role="group">
					${availableActions
						.map(
							(action, index) =>
								`<button type="button" class="btn btn-outline-primary scan-action ${
									index === 0 ? "active" : ""
								}" data-action="${action}">${__(`Mark ${action}`)}</button>`
						)
						.join("")}
				</div>
				<button class="btn btn-primary btn-lg btn-block start-scan">${__("Scan QR Code")}</button>
				<div class="scan-result mt-4"></div>
			</div>
		</div>
	`);

			const scan = (token) => {
				const action = $content.find(".scan-action.active").data("action");
				$content
					.find(".scan-result")
					.html(`<div class="text-muted">${__("Processing scan…")}</div>`);
				frappe
					.call({
						method: "vuna_transport.services.execution.delivery_note_scan_service.scan_delivery_note_qr",
						args: { token, action },
					})
					.then((response) => {
						const result = response.message;
						$content
							.find(".scan-result")
							.html(
								`<div class="alert alert-success"><strong>${frappe.utils.escape_html(
									result.delivery_note
								)}</strong><br>${frappe.utils.escape_html(result.status)}</div>`
							);
					})
					.catch(() => {
						$content
							.find(".scan-result")
							.html(
								`<div class="alert alert-danger">${__(
									"Scan could not be applied. Check the Delivery Note status and your permissions."
								)}</div>`
							);
					});
			};

			$content.find(".start-scan").on("click", () => {
				new frappe.ui.Scanner({
					dialog: true,
					on_scan(decoded_result) {
						scan(decoded_result.decodedText);
					},
				}).scan();
			});
			$content.find(".scan-action").on("click", function () {
				$content.find(".scan-action").removeClass("active");
				$(this).addClass("active");
			});
		});
};
