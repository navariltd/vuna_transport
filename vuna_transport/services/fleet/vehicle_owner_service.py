"""Shared normalization for Vehicle owner values."""


def normalize_vehicle_owner(value):
	"""Return the canonical Vehicle owner value while accepting legacy values."""
	return {
		"Company": "Company",
		"Company Owned": "Company",
		"Supplier": "Supplier",
		"Leased": "Supplier",
		"Subcontracted": "Supplier",
	}.get(value or "", value or "")
