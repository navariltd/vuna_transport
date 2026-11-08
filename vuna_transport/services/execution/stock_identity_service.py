"""Resolve batch identity across ERPNext's legacy fields and bundle model."""

from erpnext.stock.serial_batch_bundle import get_batches_from_bundle


def get_delivery_note_item_batches(item) -> list[str]:
	bundle = (
		item.get("serial_and_batch_bundle")
		if hasattr(item, "get")
		else getattr(item, "serial_and_batch_bundle", None)
	)
	if bundle:
		return list(get_batches_from_bundle(bundle))
	batch_no = item.get("batch_no") if hasattr(item, "get") else getattr(item, "batch_no", None)
	return [batch_no] if batch_no else []


def get_single_delivery_note_item_batch(item) -> str | None:
	batches = get_delivery_note_item_batches(item)
	return batches[0] if len(batches) == 1 else None
