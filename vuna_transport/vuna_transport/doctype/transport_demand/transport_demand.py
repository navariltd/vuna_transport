import frappe
from frappe.model.document import Document

from vuna_transport.services.demand.transport_demand_service import validate_transport_demand


class TransportDemand(Document):
	@frappe.whitelist()
	def create_load(self, source_warehouse: str | None = None):
		self.check_permission("read")
		from vuna_transport.services.planning.transport_load_service import (
			create_transport_load_from_demands,
			get_committed_quantities,
		)

		committed = get_committed_quantities([row.name for row in self.items])
		return create_transport_load_from_demands(
			[
				{
					"transport_demand": self.name,
					"transport_demand_item": row.name,
					"allocated_quantity": (row.stock_qty - committed.get(row.name, 0))
					/ row.conversion_factor,
				}
				for row in self.items
				if row.stock_qty > committed.get(row.name, 0)
			],
			source_warehouse=source_warehouse,
		)

	def validate(self):
		validate_transport_demand(self)

	def on_submit(self):
		self.db_set("status", "Open")
		from vuna_transport.services.planning.demand_lifecycle_service import sync_demand_allocations

		sync_demand_allocations([self.name])

	def on_cancel(self):
		self.db_set("status", "Cancelled")

	def before_cancel(self):
		from vuna_transport.services.planning.demand_lifecycle_service import lock_demands

		lock_demands([self.name])
		loads = frappe.db.sql(
			"""select distinct l.name from `tabTransport Load Item` i
			join `tabTransport Load` l on l.name = i.parent
			where i.transport_demand = %s and l.docstatus in (0, 1)""",
			self.name,
		)
		if loads:
			frappe.throw(
				frappe._("Cancel or remove linked Transport Load allocations before cancelling this demand.")
			)
