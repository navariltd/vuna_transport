import frappe
from frappe.utils import flt


def execute():
	"""Convert legacy Return Request quantities from stock UOM to Delivery Note UOM."""
	if not frappe.db.table_exists("Return Request Item"):
		return

	rows = frappe.get_all(
		"Return Request Item",
		filters={"delivery_note_item": ["is", "set"]},
		fields=[
			"name",
			"parent",
			"delivery_note_item",
			"delivered_qty",
			"requested_return_qty",
			"remaining_qty",
			"received_qty",
			"accepted_return_qty",
			"rejected_return_qty",
			"resolution_qty",
		],
		limit_page_length=0,
	)
	if not rows:
		return

	request_names = list({row.parent for row in rows})
	request_pods = {
		row.name: row.proof_of_delivery
		for row in frappe.get_all(
			"Return Request",
			filters={"name": ["in", request_names], "proof_of_delivery": ["is", "set"]},
			fields=["name", "proof_of_delivery"],
			limit_page_length=0,
		)
	}
	pod_names = list(set(request_pods.values()))
	pod_items = {}
	if pod_names:
		pod_items = {
			(row.parent, row.delivery_note_item): row
			for row in frappe.get_all(
				"Proof of Delivery Item",
				filters={
					"parent": ["in", pod_names],
					"delivery_note_item": ["is", "set"],
				},
				fields=[
					"parent",
					"delivery_note_item",
					"uom",
					"delivered_qty",
					"rejected_qty",
				],
				limit_page_length=0,
			)
		}

	delivery_item_names = list({row.delivery_note_item for row in rows})
	delivery_items = {
		row.name: row
		for row in frappe.get_all(
			"Delivery Note Item",
			filters={"name": ["in", delivery_item_names]},
			fields=["name", "uom", "stock_uom", "conversion_factor"],
			limit_page_length=0,
		)
	}
	quantity_fields = (
		"delivered_qty",
		"requested_return_qty",
		"remaining_qty",
		"received_qty",
		"accepted_return_qty",
		"rejected_return_qty",
		"resolution_qty",
	)
	for row in rows:
		delivery_item = delivery_items.get(row.delivery_note_item)
		if not delivery_item:
			continue
		pod_item = pod_items.get((request_pods.get(row.parent), row.delivery_note_item))
		values = get_sales_uom_values(row, delivery_item, pod_item, quantity_fields)
		frappe.db.set_value("Return Request Item", row.name, values, update_modified=False)


def get_sales_uom_values(row, delivery_item, pod_item, quantity_fields):
	"""Convert only legacy stock-UOM rows; newer POD rows already use the sales UOM."""
	conversion_factor = flt(delivery_item.conversion_factor) or 1
	values = {
		"uom": delivery_item.uom,
		"stock_uom": delivery_item.stock_uom,
		"conversion_factor": conversion_factor,
	}
	if pod_item and pod_item.uom:
		for fieldname in quantity_fields:
			values[fieldname] = flt(row.get(fieldname))
		values["delivered_qty"] = flt(pod_item.delivered_qty)
		values["requested_return_qty"] = flt(pod_item.rejected_qty)
		return values

	for fieldname in quantity_fields:
		values[fieldname] = flt(row.get(fieldname)) / conversion_factor
	return values
