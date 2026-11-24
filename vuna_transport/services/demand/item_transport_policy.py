"""Item eligibility for new demand; existing execution keeps its approved rows."""

import frappe
from frappe.utils import cint


def item_requires_transport(item_code):
	if not item_code:
		return False
	item = frappe.get_cached_doc("Item", item_code)
	# During pre-schema upgrades the new field is not installed yet.
	required = item.get("vuna_requires_transport")
	stock = item.get("is_stock_item")
	return bool(
		(stock is None or cint(stock))
		and not cint(item.get("disabled"))
		and (required is None or cint(required))
	)


def existing_source_items(source):
	"""Approved demand rows remain receivable if the Item is subsequently opted out."""
	return set(
		frappe.db.sql_list(
			"""select i.source_item from `tabTransport Demand Item` i
		join `tabTransport Demand` d on d.name=i.parent
		where d.source_document_type=%s and d.source_document=%s and d.docstatus=1
		and coalesce(d.migration_archived,0)=0
		and i.parenttype='Transport Demand'""",
			(source.doctype, source.name),
		)
	)
