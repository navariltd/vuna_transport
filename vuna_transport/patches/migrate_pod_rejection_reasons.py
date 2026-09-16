"""Move nested POD rejection reasons onto the parent POD Table MultiSelect."""

import frappe


def execute():
	if not frappe.db.table_exists("Rejection Reason Item"):
		return

	rows = frappe.db.sql(
		"""
		select rr.parent as pod_item, rr.rejection_reason,
			poi.parent as proof_of_delivery, poi.delivery_note_item, poi.item_code
		from `tabRejection Reason Item` rr
		join `tabProof of Delivery Item` poi on poi.name = rr.parent
		where rr.parenttype = 'Proof of Delivery Item'
		and rr.parentfield = 'rejection_reasons'
		""",
		as_dict=True,
	)
	for row in rows:
		if frappe.db.exists(
			"Rejection Reason Item",
			{
				"parent": row.proof_of_delivery,
				"parenttype": "Proof of Delivery",
				"rejection_reason": row.rejection_reason,
			},
		):
			continue
		frappe.get_doc(
			{
				"doctype": "Rejection Reason Item",
				"parent": row.proof_of_delivery,
				"parenttype": "Proof of Delivery",
				"parentfield": "rejection_reasons",
				"rejection_reason": row.rejection_reason,
			}
		).insert(ignore_permissions=True)

	frappe.db.delete(
		"Rejection Reason Item",
		{"parenttype": "Proof of Delivery Item", "parentfield": "rejection_reasons"},
	)
