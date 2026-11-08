"""Convert and verify Delivery Order history before normal model synchronization."""

from vuna_transport.services.demand.delivery_order_retirement import migrate_before_model_sync


def execute():
	migrate_before_model_sync()
