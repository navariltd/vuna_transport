frappe.ui.form.on("Vehicle Onboarding", {
	setup(frm) {
		frm.set_query("transport_cost_structure", () => ({
			filters: {
				company: ["in", [frm.doc.company, "", null]],
				vehicle_owner: frm.doc.vehicle_owner,
				disabled: 0,
				docstatus: 1,
			},
		}));
	},

	vehicle_category(frm) {
		if (!frm.doc.vehicle_category) return;

		frappe.db
			.get_value("Vehicle Category", frm.doc.vehicle_category, [
				"vehicle_model",
				"license_plate_format",
			])
			.then(({ message }) => {
				if (message?.vehicle_model) {
					frm.set_value("vehicle_model", message.vehicle_model);
				}
				set_plate_format_hint(frm, message?.license_plate_format);
				format_license_plate_field(frm, message?.license_plate_format);
			});
	},

	license_plate(frm) {
		format_license_plate_field(frm);
		const licensePlate = frm.doc.license_plate?.trim();
		if (!licensePlate) return;
		Promise.all([
			frappe.db.get_value("Vehicle", { license_plate: licensePlate }, "name"),
			frappe.db.get_value(
				"Vehicle Onboarding",
				{ license_plate: licensePlate, name: ["!=", frm.doc.name || ""] },
				"name"
			),
		]).then(([vehicle, onboarding]) => {
			if (
				frm.doc.license_plate?.trim() !== licensePlate ||
				(!vehicle.message?.name && !onboarding.message?.name)
			)
				return;

			const existing = [];
			if (vehicle.message?.name) {
				existing.push(
					__("Vehicle: {0}", [frappe.utils.escape_html(vehicle.message.name)])
				);
			}
			if (onboarding.message?.name) {
				existing.push(
					__("Vehicle Onboarding: {0}", [
						frappe.utils.escape_html(onboarding.message.name),
					])
				);
			}
			frappe.msgprint({
				indicator: "red",
				title: __("License Plate Already Exists"),
				message: existing.join("<br>"),
			});
			frm.set_focus("license_plate");
		});
	},

	vehicle_owner(frm) {
		if (frm.doc.transport_cost_structure) {
			frm.set_value("transport_cost_structure", null);
		}
		if (frm.doc.vehicle_owner === "Company" && frm.doc.supplier) {
			frm.set_value("supplier", null);
		}
	},

	transport_cost_structure(frm) {
		if (!frm.doc.transport_cost_structure) return;
		frappe.call({
			method: "vuna_transport.vuna_transport.doctype.vehicle_onboarding.vehicle_onboarding.get_onboarding_template_documents",
			args: { transport_cost_structure: frm.doc.transport_cost_structure },
			freeze: true,
			freeze_message: __("Loading onboarding documents..."),
			callback: ({ message }) => {
				if (!message?.length) {
					frappe.show_alert({
						message: __(
							"No onboarding template is configured for this cost structure."
						),
						indicator: "orange",
					});
					return;
				}
				populate_template_documents(frm, message);
			},
		});
	},
});

function set_plate_format_hint(frm, format) {
	const example = format === "Motorcycle" ? "KMCE 123A" : "KAA 123A";
	frm.set_df_property(
		"license_plate",
		"description",
		__("Use the {0} format, for example {1}.", [format || "Motor Vehicle", example])
	);
}

function format_license_plate_field(frm, format) {
	const value = frm.doc.license_plate?.replace(/[\s-]+/g, "").toUpperCase();
	if (!value) return;
	const splitAt = format === "Motorcycle" ? 4 : 3;
	const expectedLength = format === "Motorcycle" ? 8 : 7;
	if (value.length === expectedLength) {
		const formatted = `${value.slice(0, splitAt)} ${value.slice(splitAt)}`;
		if (frm.doc.license_plate !== formatted) frm.set_value("license_plate", formatted);
	}
}

function populate_template_documents(frm, documents) {
	const replace = () => {
		frm.clear_table("documents");
		documents.forEach((row) => {
			frm.add_child("documents", { document_type: row.vehicle_document_type });
		});
		frm.refresh_field("documents");
		frm.dirty();
	};

	if ((frm.doc.documents || []).some((row) => row.document_type)) {
		frappe.confirm(
			__("Replace the current onboarding documents with this cost structure's template?"),
			replace
		);
		return;
	}
	replace();
}
