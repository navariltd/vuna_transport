frappe.ui.form.on("Address", {
	vuna_latitude(frm) {
		sync_map_from_coordinates(frm);
	},

	vuna_longitude(frm) {
		sync_map_from_coordinates(frm);
	},

	vuna_location(frm) {
		sync_coordinates_from_map(frm);
	},

	vuna_use_device_location(frm) {
		use_device_location(frm);
	},
});

function sync_map_from_coordinates(frm) {
	if (frm.__vuna_syncing_location) return;
	const latitude = coordinate_value(frm.doc.vuna_latitude);
	const longitude = coordinate_value(frm.doc.vuna_longitude);

	if (latitude === null && longitude === null) {
		set_location_fields(frm, null, null, null);
		return;
	}
	if (latitude === null || longitude === null) return;
	if (!coordinates_are_valid(latitude, longitude, true)) return;

	const current = point_coordinates(frm.doc.vuna_location);
	if (current && coordinates_match(current.latitude, current.longitude, latitude, longitude)) {
		return;
	}
	set_location_fields(frm, latitude, longitude, point_geojson(latitude, longitude));
}

function sync_coordinates_from_map(frm) {
	if (frm.__vuna_syncing_location) return;
	const point = point_coordinates(frm.doc.vuna_location);
	if (point) {
		set_location_fields(frm, point.latitude, point.longitude, frm.doc.vuna_location);
		return;
	}
	if (!frm.doc.vuna_location) {
		set_location_fields(frm, null, null, null);
		return;
	}
	frappe.msgprint({
		title: __("Delivery Map Pin Required"),
		indicator: "orange",
		message: __("Delivery Location must contain a point marker. Remove other shapes and place one pin."),
	});
}

function use_device_location(frm) {
	if (!navigator.geolocation) {
		frappe.msgprint(__("Location services are not supported by this browser or device."));
		return;
	}

	frappe.show_alert({ message: __("Requesting the device location..."), indicator: "blue" });
	navigator.geolocation.getCurrentPosition(
		(position) => {
			const latitude = position.coords.latitude;
			const longitude = position.coords.longitude;
			if (!coordinates_are_valid(latitude, longitude, false)) return;
			set_location_fields(frm, latitude, longitude, point_geojson(latitude, longitude));
			frappe.show_alert({
				message: __("Delivery location pinned (accuracy approximately {0} metres).", [
					Math.round(position.coords.accuracy || 0),
				]),
				indicator: "green",
			});
		},
		(error) => {
			const messages = {
				1: __("Location permission was denied. Allow location access in the browser and try again."),
				2: __("The device location is currently unavailable."),
				3: __("The device location request timed out. Try again where GPS or network reception is stronger."),
			};
			frappe.msgprint({
				title: __("Unable to Pin Device Location"),
				indicator: "orange",
				message: messages[error.code] || __("The device location could not be determined."),
			});
		},
		{
			enableHighAccuracy: true,
			timeout: 15000,
			maximumAge: 60000,
		},
	);
}

function set_location_fields(frm, latitude, longitude, location) {
	frm.__vuna_syncing_location = true;
	Promise.all([
		frm.set_value("vuna_latitude", latitude),
		frm.set_value("vuna_longitude", longitude),
		frm.set_value("vuna_location", location),
	]).finally(() => {
		frm.__vuna_syncing_location = false;
	});
}

function coordinate_value(value) {
	if (value === null || value === undefined || value === "") return null;
	const number = Number(value);
	return Number.isFinite(number) ? number : null;
}

function coordinates_are_valid(latitude, longitude, show_message) {
	const valid = latitude >= -90 && latitude <= 90 && longitude >= -180 && longitude <= 180;
	if (!valid && show_message) {
		frappe.show_alert({
			message: __("Latitude must be between -90 and 90, and longitude between -180 and 180."),
			indicator: "orange",
		});
	}
	return valid;
}

function point_geojson(latitude, longitude) {
	return JSON.stringify({
		type: "FeatureCollection",
		features: [
			{
				type: "Feature",
				properties: {},
				geometry: {
					type: "Point",
					coordinates: [longitude, latitude],
				},
			},
		],
	});
}

function point_coordinates(location) {
	if (!location) return null;
	try {
		const geojson = typeof location === "string" ? JSON.parse(location) : location;
		const feature = (geojson.features || []).find(
			(row) => row.geometry?.type === "Point" && row.geometry.coordinates?.length >= 2,
		);
		if (!feature) return null;
		return {
			latitude: Number(feature.geometry.coordinates[1]),
			longitude: Number(feature.geometry.coordinates[0]),
		};
	} catch (error) {
		return null;
	}
}

function coordinates_match(first_latitude, first_longitude, second_latitude, second_longitude) {
	return (
		Math.abs(first_latitude - second_latitude) < 0.00000001 &&
		Math.abs(first_longitude - second_longitude) < 0.00000001
	);
}
