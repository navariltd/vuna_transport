"""Idempotent dispatch demo data for development and manual testing only."""

import json

import frappe
from frappe.contacts.doctype.address.address import get_address_display
from frappe.utils import add_days, flt, nowdate, nowtime

from vuna_transport.services.routing.location_service import set_delivery_order_location_snapshots

DEMO_PREFIX = "VT-DEMO"
COMPANY = "Yovuna Technologies Limited (Demo)"
NAIROBI_WAREHOUSE = "Nairobi Dispatch - YTLD"
MERU_WAREHOUSE = "Meru Dispatch - YTLD"


def generate_dispatch_test_data():
	"""Create planning masters, stock and submitted Sales Orders; nothing downstream."""
	_validate_prerequisites()
	settings = frappe.get_single("Transport Settings")
	settings.enabled = 1
	settings.auto_create_delivery_orders = 1
	settings.require_sales_order_submission = 1
	settings.default_weight_uom = "Kg"
	settings.default_volume_uom = "Cubic Meter"
	settings.load_suggestion_option_count = max(settings.load_suggestion_option_count or 3, 3)
	settings.save(ignore_permissions=True)

	zones = _ensure_zones()
	delivery_method = _ensure_delivery_method()
	vehicle_categories = _ensure_vehicle_categories()
	addresses = _ensure_customers_and_addresses(zones, vehicle_categories)
	_ensure_warehouse_addresses()
	vehicles = _ensure_vehicles(vehicle_categories)
	items = _ensure_items()
	stock_reconciliations = _ensure_stock(items)
	sales_orders = _ensure_sales_orders(addresses, items, delivery_method)
	_refresh_demo_draft_delivery_orders(sales_orders)
	delivery_orders = frappe.get_all(
		"Delivery Order",
		filters={"sales_order": ["in", sales_orders]},
		fields=[
			"name",
			"sales_order",
			"customer_name",
			"source_warehouse",
			"required_delivery_date",
			"transport_zone",
			"total_weight",
			"total_volume",
			"docstatus",
		],
		order_by="required_delivery_date asc, source_warehouse asc, creation asc",
	)
	frappe.db.commit()
	return {
		"company": COMPANY,
		"warehouses": [NAIROBI_WAREHOUSE, MERU_WAREHOUSE],
		"zones": zones,
		"customers": [record["customer"] for record in addresses.values()],
		"addresses": {label: record["address"] for label, record in addresses.items()},
		"items": list(items),
		"vehicles": vehicles,
		"stock_reconciliations_created": stock_reconciliations,
		"submitted_sales_orders": sales_orders,
		"generated_delivery_orders": delivery_orders,
		"downstream_documents_created": {
			"delivery_loads": _count_linked_documents("Delivery Load", "delivery_orders", sales_orders),
			"delivery_notes": frappe.db.count("Delivery Note", {"po_no": ["like", f"{DEMO_PREFIX}%"]})
			if frappe.get_meta("Delivery Note").has_field("po_no")
			else 0,
			"delivery_trips": 0,
		},
	}


def _validate_prerequisites():
	if not frappe.db.exists("Company", COMPANY):
		frappe.throw(f"Company {COMPANY} does not exist.")
	for warehouse in (NAIROBI_WAREHOUSE, MERU_WAREHOUSE):
		if not frappe.db.exists("Warehouse", warehouse):
			frappe.throw(f"Warehouse {warehouse} does not exist.")


def _ensure_zones():
	definitions = (
		("Nairobi Central", "NBO-CENTRAL", "CBD, Westlands and Kilimani delivery cluster."),
		("Nairobi East", "NBO-EAST", "Industrial Area, Embakasi and Mombasa Road cluster."),
		("Kiambu South", "KIAMBU-SOUTH", "Ruiru and nearby Kiambu delivery cluster."),
	)
	zones = {}
	for zone_name, zone_code, description in definitions:
		name = frappe.db.exists("Transport Zone", {"zone_code": zone_code})
		if name:
			doc = frappe.get_doc("Transport Zone", name)
		else:
			doc = frappe.new_doc("Transport Zone")
		doc.zone_name = zone_name
		doc.zone_code = zone_code
		doc.description = description
		doc.disabled = 0
		doc.is_group = 0
		doc.save(ignore_permissions=True)
		zones[zone_code] = doc.name
	return zones


def _ensure_delivery_method():
	name = "Company Delivery"
	doc = (
		frappe.get_doc("Delivery Method", name)
		if frappe.db.exists("Delivery Method", name)
		else frappe.new_doc("Delivery Method")
	)
	doc.delivery_method_name = name
	doc.fulfilment_type = "Company Delivery"
	doc.requires_transport_planning = 1
	doc.requires_vehicle_allocation = 1
	doc.requires_delivery_trip = 1
	doc.disabled = 0
	doc.save(ignore_permissions=True)
	return doc.name


def _ensure_customers_and_addresses(zones, vehicle_categories):
	definitions = (
		("VT Demo CBD Supermarket", "NBO-CENTRAL", "Moi Avenue", "Nairobi", -1.2833, 36.8219),
		("VT Demo Westlands Mart", "NBO-CENTRAL", "Waiyaki Way, Westlands", "Nairobi", -1.2676, 36.8108),
		("VT Demo Kilimani Foods", "NBO-CENTRAL", "Ngong Road, Kilimani", "Nairobi", -1.2921, 36.7839),
		(
			"VT Demo Embakasi Wholesalers",
			"NBO-EAST",
			"Airport North Road, Embakasi",
			"Nairobi",
			-1.3167,
			36.8953,
		),
		(
			"VT Demo Industrial Distributors",
			"NBO-EAST",
			"Enterprise Road, Industrial Area",
			"Nairobi",
			-1.3075,
			36.8522,
		),
		("VT Demo Mombasa Road Depot", "NBO-EAST", "Mombasa Road", "Nairobi", -1.3297, 36.8730),
		("VT Demo Ruiru Retail", "KIAMBU-SOUTH", "Thika Road, Ruiru", "Ruiru", -1.1457, 36.9602),
		("VT Demo Kiambu Cash & Carry", "KIAMBU-SOUTH", "Kiambu Road", "Kiambu", -1.1714, 36.8356),
	)
	addresses = {}
	windows = {
		"NBO-CENTRAL": ("08:00:00", "11:00:00"),
		"NBO-EAST": ("09:00:00", "13:00:00"),
		"KIAMBU-SOUTH": ("10:00:00", "14:00:00"),
	}
	maximum_categories = {
		"VT Demo CBD Supermarket": "8 Ton Box Truck",
		"VT Demo Westlands Mart": "5 Ton Box Truck",
		"VT Demo Kilimani Foods": "8 Ton Refrigerated Truck",
		"VT Demo Embakasi Wholesalers": "15 Ton Truck",
		"VT Demo Industrial Distributors": "15 Ton Truck",
		"VT Demo Mombasa Road Depot": "8 Ton Box Truck",
		"VT Demo Ruiru Retail": "8 Ton Box Truck",
		"VT Demo Kiambu Cash & Carry": "5 Ton Box Truck",
	}
	for customer_name, zone_code, address_line, city, latitude, longitude in definitions:
		customer = _ensure_customer(customer_name)
		address_name = frappe.db.exists(
			"Address",
			{"address_title": customer_name, "address_type": "Shipping"},
		)
		address = frappe.get_doc("Address", address_name) if address_name else frappe.new_doc("Address")
		address.address_title = customer_name
		address.address_type = "Shipping"
		address.address_line1 = address_line
		address.city = city
		address.county = "Kenya"
		address.country = "Kenya"
		address.pincode = "00100"
		address.vuna_delivery_zone = zones[zone_code]
		address.vuna_location = _point_geojson(latitude, longitude)
		address.vuna_preferred_delivery_from = windows[zone_code][0]
		address.vuna_preferred_delivery_to = windows[zone_code][1]
		address.vuna_average_service_duration = 1800
		address.vuna_maximum_vehicle_category = vehicle_categories[maximum_categories[customer_name]]
		address.vuna_requires_tail_lift = customer_name in (
			"VT Demo Westlands Mart",
			"VT Demo Kiambu Cash & Carry",
		)
		address.vuna_loading_dock_available = customer_name in (
			"VT Demo Embakasi Wholesalers",
			"VT Demo Industrial Distributors",
		)
		address.vuna_restricted_access = customer_name in (
			"VT Demo CBD Supermarket",
			"VT Demo Kilimani Foods",
		)
		address.vuna_access_notes = (
			"Confirm access with receiving team before dispatch." if address.vuna_restricted_access else None
		)
		address.is_shipping_address = 1
		address.is_primary_address = 1
		address.set("links", [])
		address.append("links", {"link_doctype": "Customer", "link_name": customer})
		address.save(ignore_permissions=True)
		addresses[customer_name] = {
			"customer": customer,
			"address": address.name,
		}
	return addresses


def _ensure_customer(customer_name):
	name = frappe.db.exists("Customer", {"customer_name": customer_name})
	doc = frappe.get_doc("Customer", name) if name else frappe.new_doc("Customer")
	doc.customer_name = customer_name
	doc.customer_type = "Company"
	doc.customer_group = "Commercial"
	doc.territory = "Kenya"
	doc.default_price_list = "Standard Selling"
	doc.disabled = 0
	doc.save(ignore_permissions=True)
	return doc.name


def _ensure_warehouse_addresses():
	definitions = (
		(
			NAIROBI_WAREHOUSE,
			"VT Demo Nairobi Dispatch Warehouse",
			"Enterprise Road",
			"Nairobi",
			-1.3102,
			36.8559,
		),
		(MERU_WAREHOUSE, "VT Demo Meru Dispatch Warehouse", "Meru-Nanyuki Road", "Meru", 0.0515, 37.6456),
	)
	for warehouse, title, address_line, city, latitude, longitude in definitions:
		name = frappe.db.exists("Address", {"address_title": title, "address_type": "Office"})
		address = frappe.get_doc("Address", name) if name else frappe.new_doc("Address")
		address.address_title = title
		address.address_type = "Office"
		address.address_line1 = address_line
		address.city = city
		address.country = "Kenya"
		address.pincode = "00100"
		address.vuna_location = _point_geojson(latitude, longitude)
		address.is_primary_address = 1
		address.set("links", [])
		address.append("links", {"link_doctype": "Warehouse", "link_name": warehouse})
		address.save(ignore_permissions=True)


def _ensure_vehicle_categories():
	definitions = (
		("3 Ton Van", 3000, 12, 6, 0, 0, None),
		("5 Ton Box Truck", 5000, 20, 10, 0, 1, None),
		("8 Ton Box Truck", 8000, 30, 16, 0, 1, None),
		("15 Ton Truck", 15000, 50, 28, 0, 1, None),
		("8 Ton Refrigerated Truck", 8000, 28, 14, 1, 1, "Temperature Logger"),
	)
	result = {}
	for label, payload, volume, pallets, refrigerated, tail_lift, equipment in definitions:
		name = frappe.db.exists("Vehicle Category", {"vehicle_category_name": label})
		doc = frappe.get_doc("Vehicle Category", name) if name else frappe.new_doc("Vehicle Category")
		doc.vehicle_category_name = label
		doc.body_type = "Van" if "Van" in label else "Rigid Truck"
		doc.max_payload_weight = payload
		doc.max_payload_weight_uom = "Kg"
		doc.max_volume = volume
		doc.max_volume_uom = "Cubic Meter"
		doc.max_pallets = pallets
		doc.max_cases = 1000
		doc.max_crates = 500
		doc.closed_body = 1
		doc.refrigerated = refrigerated
		doc.tail_lift = tail_lift
		doc.special_equipment = equipment
		doc.disabled = 0
		doc.save(ignore_permissions=True)
		result[label] = doc.name
	return result


def _ensure_vehicles(categories):
	definitions = (
		("KVT 300A", "3 Ton Van"),
		("KVT 301A", "3 Ton Van"),
		("KVT 500B", "5 Ton Box Truck"),
		("KVT 800C", "8 Ton Box Truck"),
		("KVT 801C", "8 Ton Box Truck"),
		("KVT 150D", "15 Ton Truck"),
		("KVT 800R", "8 Ton Refrigerated Truck"),
	)
	vehicles = []
	for registration, category_label in definitions:
		doc = (
			frappe.get_doc("Vehicle", registration)
			if frappe.db.exists("Vehicle", registration)
			else frappe.new_doc("Vehicle")
		)
		doc.license_plate = registration
		doc.make = "Isuzu"
		doc.model = category_label
		doc.company = COMPANY
		doc.last_odometer = doc.last_odometer or 1000
		doc.fuel_type = "Diesel"
		doc.uom = "Litre"
		doc.vuna_vehicle_category = categories[category_label]
		doc.vuna_transport_enabled = 1
		doc.vuna_vehicle_owner = "Company"
		doc.save(ignore_permissions=True)
		vehicles.append(doc.name)
	return vehicles


def _ensure_items():
	definitions = (
		("VT-DEMO-WATER-CASE", "Bottled Water 500ml Case", 12.5, 0.015, 650, 0, 0, None),
		("VT-DEMO-FLOUR-BOX", "Fortified Flour Box", 20, 0.035, 1800, 0, 0, None),
		("VT-DEMO-OIL-BOX", "Cooking Oil Box", 18, 0.028, 2400, 0, 0, None),
		("VT-DEMO-TISSUE-BALE", "Tissue Paper Bale", 8, 0.120, 950, 0, 0, None),
		("VT-DEMO-JUICE-CRATE", "Chilled Fruit Juice Crate", 14, 0.045, 1600, 1, 0, "Temperature Logger"),
	)
	items = {}
	for item_code, item_name, weight, volume, rate, refrigerated, tail_lift, equipment in definitions:
		doc = (
			frappe.get_doc("Item", item_code)
			if frappe.db.exists("Item", item_code)
			else frappe.new_doc("Item")
		)
		doc.item_code = item_code
		doc.item_name = item_name
		doc.item_group = "Products"
		doc.stock_uom = "Box"
		doc.is_stock_item = 1
		doc.is_sales_item = 1
		doc.is_purchase_item = 1
		doc.disabled = 0
		doc.weight_per_unit = weight
		doc.weight_uom = "Kg"
		doc.vuna_volume_per_stock_uom = volume
		doc.vuna_volume_uom = "Cubic Meter"
		doc.vuna_refrigeration_required = refrigerated
		doc.vuna_tail_lift_required = tail_lift
		doc.vuna_special_equipment_required = equipment
		doc.standard_rate = rate
		doc.save(ignore_permissions=True)
		_ensure_item_price(item_code, rate)
		items[item_code] = {"rate": rate, "valuation_rate": rate * 0.65}
	return items


def _ensure_item_price(item_code, rate):
	name = frappe.db.exists(
		"Item Price", {"item_code": item_code, "price_list": "Standard Selling", "uom": "Box"}
	)
	doc = frappe.get_doc("Item Price", name) if name else frappe.new_doc("Item Price")
	doc.price_list = "Standard Selling"
	doc.item_code = item_code
	doc.uom = "Box"
	doc.price_list_rate = rate
	doc.currency = "KES"
	doc.selling = 1
	doc.save(ignore_permissions=True)


def _ensure_stock(items):
	target_quantity = 5000
	rows = []
	for warehouse in (NAIROBI_WAREHOUSE, MERU_WAREHOUSE):
		for item_code, values in items.items():
			actual = flt(
				frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty")
			)
			if actual < target_quantity:
				rows.append(
					{
						"item_code": item_code,
						"warehouse": warehouse,
						"qty": target_quantity,
						"valuation_rate": values["valuation_rate"],
					}
				)
	if not rows:
		return []
	company = frappe.get_cached_doc("Company", COMPANY)
	doc = frappe.new_doc("Stock Reconciliation")
	doc.company = COMPANY
	doc.purpose = "Stock Reconciliation"
	doc.posting_date = nowdate()
	doc.posting_time = nowtime()
	doc.set_posting_time = 1
	doc.expense_account = company.default_expense_account
	doc.cost_center = company.cost_center
	for row in rows:
		doc.append("items", row)
	doc.insert(ignore_permissions=True)
	doc.submit()
	return [doc.name]


def _ensure_sales_orders(addresses, items, delivery_method):
	first_date = add_days(nowdate(), 2)
	second_date = add_days(nowdate(), 3)
	orders = (
		(
			"01",
			"VT Demo CBD Supermarket",
			first_date,
			(("VT-DEMO-WATER-CASE", 160, NAIROBI_WAREHOUSE), ("VT-DEMO-TISSUE-BALE", 40, NAIROBI_WAREHOUSE)),
		),
		(
			"02",
			"VT Demo Westlands Mart",
			first_date,
			(("VT-DEMO-FLOUR-BOX", 100, NAIROBI_WAREHOUSE), ("VT-DEMO-OIL-BOX", 80, NAIROBI_WAREHOUSE)),
		),
		("03", "VT Demo Kilimani Foods", first_date, (("VT-DEMO-JUICE-CRATE", 120, NAIROBI_WAREHOUSE),)),
		(
			"04",
			"VT Demo Embakasi Wholesalers",
			first_date,
			(("VT-DEMO-WATER-CASE", 240, NAIROBI_WAREHOUSE), ("VT-DEMO-TISSUE-BALE", 100, NAIROBI_WAREHOUSE)),
		),
		(
			"05",
			"VT Demo Industrial Distributors",
			first_date,
			(("VT-DEMO-FLOUR-BOX", 150, NAIROBI_WAREHOUSE), ("VT-DEMO-OIL-BOX", 100, NAIROBI_WAREHOUSE)),
		),
		("06", "VT Demo Mombasa Road Depot", first_date, (("VT-DEMO-JUICE-CRATE", 200, NAIROBI_WAREHOUSE),)),
		(
			"07",
			"VT Demo Ruiru Retail",
			second_date,
			(("VT-DEMO-WATER-CASE", 120, NAIROBI_WAREHOUSE), ("VT-DEMO-FLOUR-BOX", 80, NAIROBI_WAREHOUSE)),
		),
		(
			"08",
			"VT Demo Kiambu Cash & Carry",
			second_date,
			(("VT-DEMO-OIL-BOX", 160, NAIROBI_WAREHOUSE), ("VT-DEMO-TISSUE-BALE", 50, NAIROBI_WAREHOUSE)),
		),
		(
			"09",
			"VT Demo CBD Supermarket",
			second_date,
			(("VT-DEMO-WATER-CASE", 80, NAIROBI_WAREHOUSE), ("VT-DEMO-FLOUR-BOX", 60, MERU_WAREHOUSE)),
		),
	)
	created = []
	for sequence, customer, delivery_date, order_items in orders:
		po_no = f"{DEMO_PREFIX}-SO-{sequence}"
		existing = frappe.db.exists(
			"Sales Order", {"company": COMPANY, "po_no": po_no, "docstatus": ["!=", 2]}
		)
		if existing:
			doc = frappe.get_doc("Sales Order", existing)
			if doc.docstatus == 1:
				created.append(doc.name)
				continue
		else:
			doc = frappe.new_doc("Sales Order")
		doc.company = COMPANY
		customer_record = addresses[customer]
		doc.customer = customer_record["customer"]
		doc.transaction_date = nowdate()
		doc.delivery_date = delivery_date
		doc.po_no = po_no
		doc.order_type = "Sales"
		doc.currency = "KES"
		doc.selling_price_list = "Standard Selling"
		doc.price_list_currency = "KES"
		doc.conversion_rate = 1
		doc.plc_conversion_rate = 1
		doc.territory = "Kenya"
		doc.vuna_delivery_method = delivery_method
		doc.customer_address = customer_record["address"]
		doc.shipping_address_name = customer_record["address"]
		doc.address_display = get_address_display(
			frappe.get_doc("Address", customer_record["address"]).as_dict()
		)
		doc.shipping_address = doc.address_display
		doc.set("items", [])
		for item_code, quantity, warehouse in order_items:
			doc.append(
				"items",
				{
					"item_code": item_code,
					"qty": quantity,
					"uom": "Box",
					"conversion_factor": 1,
					"rate": items[item_code]["rate"],
					"delivery_date": delivery_date,
					"warehouse": warehouse,
				},
			)
		doc.set_missing_values()
		doc.calculate_taxes_and_totals()
		doc.save(ignore_permissions=True)
		doc.submit()
		created.append(doc.name)
	return created


def _refresh_demo_draft_delivery_orders(sales_orders):
	"""Refresh only unsubmitted demo snapshots after master-data enhancements."""
	for name in frappe.get_all(
		"Delivery Order",
		filters={"sales_order": ["in", sales_orders], "docstatus": 0},
		pluck="name",
	):
		doc = frappe.get_doc("Delivery Order", name)
		item_masters = [frappe.get_cached_doc("Item", row.item_code) for row in doc.items]
		body_types = {item.vuna_required_body_type for item in item_masters if item.vuna_required_body_type}
		doc.refrigeration_required = any(item.vuna_refrigeration_required for item in item_masters)
		doc.tail_lift_required = any(item.vuna_tail_lift_required for item in item_masters)
		doc.required_body_type = next(iter(body_types), None) if len(body_types) <= 1 else None
		doc.special_equipment_required = ", ".join(
			sorted(
				{
					value.strip()
					for item in item_masters
					for value in (item.vuna_special_equipment_required or "").replace("\n", ",").split(",")
					if value.strip()
				}
			)
		)
		set_delivery_order_location_snapshots(doc, overwrite=True)
		doc.save(ignore_permissions=True)


def _point_geojson(latitude, longitude):
	return json.dumps(
		{
			"type": "FeatureCollection",
			"features": [
				{
					"type": "Feature",
					"properties": {},
					"geometry": {
						"type": "Point",
						"coordinates": [longitude, latitude],
					},
				}
			],
		}
	)


def _count_linked_documents(doctype, child_fieldname, sales_orders):
	if doctype != "Delivery Load":
		return 0
	return frappe.db.sql(
		"""
			select count(distinct parent)
			from `tabDelivery Load Order`
			where sales_order in %(sales_orders)s
		""",
		{"sales_orders": sales_orders},
	)[0][0]
