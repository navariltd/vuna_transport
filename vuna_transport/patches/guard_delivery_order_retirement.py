"""Block an unsafe upgrade before Frappe synchronizes/removes legacy DocTypes."""

from vuna_transport.services.demand.delivery_order_retirement import guard_upgrade


def execute():
	guard_upgrade()
