"""Extend Delivery Trip connections with Vuna transport documents."""


def get_data(data):
	data["non_standard_fieldnames"].update(
		{
			"Transport Load": "delivery_trip",
			"Vehicle Allocation": "vuna_current_vehicle_allocation",
			"Transport Trip Task": "delivery_trip",
			"Proof of Delivery": "delivery_trip",
		}
	)
	data.setdefault("transactions", []).append({"label": "Transport Tasks", "items": ["Transport Trip Task"]})
	data["transactions"].append({"label": "Execution Evidence", "items": ["Proof of Delivery"]})
	return data
