"""Extend Delivery Trip connections with Vuna transport documents."""


def get_data(data):
	data["non_standard_fieldnames"].update(
		{
			"Transport Load": "delivery_trip",
			"Vehicle Allocation": "vuna_current_vehicle_allocation",
		}
	)
	return data
