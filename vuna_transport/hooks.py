app_name = "vuna_transport"
app_title = "Vuna Transport"
app_publisher = "Yovuna Technologies Limited"
app_description = "Transport Management"
app_email = "solutions@navari.co.ke"
app_license = "agpl-3.0"

# Apps
# ------------------

required_apps = ["frappe/erpnext", "frappe/hrms"]

# Each item in the list will be shown as an app in the apps page
app_logo_url = "/assets/vuna_transport/images/vuna_transport.png"
app_icon_title = "Vuna Transport"

add_to_apps_screen = [
	{
		"name": app_name,
		"logo": app_logo_url,
		"title": app_title,
		"route": "/desk/vuna_transport",
		"has_permission": "vuna_transport.permissions.has_app_permission",
	}
]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/vuna_transport/css/vuna_transport.css"
# app_include_js = "/assets/vuna_transport/js/vuna_transport.js"

# include js, css files in header of web template
# web_include_css = "/assets/vuna_transport/css/vuna_transport.css"
# web_include_js = "/assets/vuna_transport/js/vuna_transport.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "vuna_transport/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "vuna_transport/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
jinja = {
	"methods": [
		"vuna_transport.services.execution.delivery_note_scan_service.get_delivery_note_qr_html",
	]
}

# Installation
# ------------

# before_install = "vuna_transport.install.before_install"
after_install = "vuna_transport.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "vuna_transport.uninstall.before_uninstall"
# after_uninstall = "vuna_transport.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "vuna_transport.utils.before_app_install"
# after_app_install = "vuna_transport.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "vuna_transport.utils.before_app_uninstall"
# after_app_uninstall = "vuna_transport.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "vuna_transport.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "vuna_transport.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Territory": {
		"validate": "vuna_transport.services.routing.location_service.validate_reference_coordinates",
	},
	"County": {
		"validate": "vuna_transport.services.routing.location_service.validate_reference_coordinates",
	},
	"Constituency": {
		"validate": "vuna_transport.services.routing.location_service.validate_reference_coordinates",
	},
	"Town": {
		"validate": "vuna_transport.services.routing.location_service.validate_reference_coordinates",
	},
	"Address": {
		"validate": "vuna_transport.services.routing.location_service.synchronize_address_location",
	},
	"Sales Order": {
		"before_submit": "vuna_transport.services.execution.return_service.validate_linked_replacement_sales_order",
		"on_submit": [
			"vuna_transport.services.demand.delivery_order_service.create_delivery_orders_for_sales_order",
			"vuna_transport.services.execution.return_service.sync_return_request_from_commercial_document",
		],
		"on_update_after_submit": "vuna_transport.services.demand.delivery_order_service.sync_delivery_orders_for_sales_order",
		"on_cancel": "vuna_transport.services.execution.return_service.sync_return_request_from_commercial_document",
	},
	"Delivery Note": {
		"validate": [
			"vuna_transport.services.execution.lineage_service.populate_delivery_note_delivery_links",
			"vuna_transport.services.execution.return_service.validate_sales_return_request_link",
		],
		"on_update": [
			"vuna_transport.services.execution.return_service.sync_sales_return_request_link",
			"vuna_transport.services.fleet.vehicle_allocation_service.sync_allocation_execution_status_for_delivery_note",
		],
		"before_submit": "vuna_transport.services.execution.return_service.validate_linked_sales_return_submission",
		"before_cancel": "vuna_transport.services.execution.return_service.prepare_sales_return_cancellation",
		"on_submit": [
			"vuna_transport.services.demand.delivery_order_service.sync_delivery_orders_after_direct_delivery_note",
			"vuna_transport.services.execution.lineage_service.complete_load_without_trip",
			"vuna_transport.services.fleet.vehicle_allocation_service.sync_allocation_execution_status_for_delivery_note",
			"vuna_transport.services.execution.return_service.sync_return_request_from_sales_return",
		],
		"on_cancel": [
			"vuna_transport.services.execution.lineage_service.reconcile_load_after_delivery_note_cancel",
			"vuna_transport.services.fleet.vehicle_allocation_service.sync_allocation_execution_status_for_delivery_note",
			"vuna_transport.services.execution.return_service.sync_return_request_from_sales_return",
		],
		"on_trash": "vuna_transport.services.execution.return_service.prepare_sales_return_cancellation",
	},
	"Quality Inspection": {
		"on_update": "vuna_transport.services.execution.return_v2_service.sync_return_quality_inspection",
		"before_cancel": [
			"vuna_transport.services.execution.return_service.prepare_quality_inspection_removal",
			"vuna_transport.services.execution.return_v2_service.prepare_return_quality_inspection_removal",
		],
		"on_submit": [
			"vuna_transport.services.execution.return_service.sync_return_request_from_quality_inspection",
			"vuna_transport.services.execution.return_v2_service.sync_return_quality_inspection",
		],
		"on_cancel": "vuna_transport.services.execution.return_service.sync_return_request_from_quality_inspection",
		"on_trash": [
			"vuna_transport.services.execution.return_service.prepare_quality_inspection_removal",
			"vuna_transport.services.execution.return_v2_service.prepare_return_quality_inspection_removal",
		],
	},
	"Sales Invoice": {
		"before_submit": "vuna_transport.services.execution.return_service.validate_linked_sales_invoice_credit_note_submission",
		"on_submit": "vuna_transport.services.execution.return_service.sync_return_request_from_commercial_document",
		"on_cancel": "vuna_transport.services.execution.return_service.sync_return_request_from_commercial_document",
	},
	"Payment Entry": {
		"on_submit": "vuna_transport.services.execution.return_service.sync_return_request_from_commercial_document",
		"on_cancel": "vuna_transport.services.execution.return_service.sync_return_request_from_commercial_document",
	},
	"Stock Entry": {
		"before_submit": "vuna_transport.services.execution.return_v2_service.validate_return_stock_entry",
		"before_cancel": "vuna_transport.services.execution.return_v2_service.prepare_return_stock_entry_removal",
		"on_submit": "vuna_transport.services.execution.return_v2_service.sync_return_inspection_from_stock_entry",
		"on_cancel": "vuna_transport.services.execution.return_v2_service.sync_return_inspection_from_stock_entry",
		"on_trash": "vuna_transport.services.execution.return_v2_service.prepare_return_stock_entry_removal",
	},
	"Delivery Trip": {
		"before_cancel": "vuna_transport.services.execution.lineage_service.prepare_delivery_trip_cancellation",
		"validate": "vuna_transport.services.execution.lineage_service.validate_delivery_trip",
		"on_update": [
			"vuna_transport.services.execution.lineage_service.link_delivery_trip",
			"vuna_transport.services.fleet.vehicle_allocation_service.sync_allocation_execution_status_for_delivery_trip",
			"vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
		],
		"on_submit": [
			"vuna_transport.services.execution.lineage_service.submit_delivery_trip",
			"vuna_transport.services.fleet.vehicle_allocation_service.sync_allocation_execution_status_for_delivery_trip",
			"vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
			"vuna_transport.services.mobile.mobile_notification_service.notify_delivery_trip",
		],
		"on_update_after_submit": [
			"vuna_transport.services.mobile.mobile_trip_execution_service.synchronize_mobile_trip_execution_status",
			"vuna_transport.services.fleet.vehicle_allocation_service.sync_allocation_execution_status_for_delivery_trip",
			"vuna_transport.services.mobile.mobile_notification_service.notify_delivery_trip",
		],
		"on_cancel": [
			"vuna_transport.services.execution.lineage_service.cancel_delivery_trip",
			"vuna_transport.services.execution.lineage_service.unlink_delivery_trip",
			"vuna_transport.services.fleet.vehicle_allocation_service.sync_allocation_execution_status_for_delivery_trip",
			"vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
		],
		"on_trash": [
			"vuna_transport.services.execution.lineage_service.unlink_delivery_trip",
		],
		"after_delete": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
	},
	"Expense Claim": {
		"on_update": "vuna_transport.services.mobile.mobile_notification_service.notify_expense_claim",
	},
	"Material Request": {
		"on_update": "vuna_transport.services.mobile.mobile_notification_service.notify_material_request",
	},
	"Vehicle": {
		"validate": "vuna_transport.services.fleet.fleet_service.validate_vehicle",
		"on_update": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
	},
	"Vehicle Allocation": {
		"on_update": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
		"on_submit": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
		"on_cancel": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
	},
	"Transport Load": {
		"on_update": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
	},
	"Asset": {
		"on_update": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
	},
	"Asset Repair": {
		"on_update": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
		"on_submit": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
		"on_cancel": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
		"after_delete": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
	},
	"Asset Maintenance Log": {
		"on_update": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
		"on_submit": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
		"on_cancel": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
		"after_delete": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
	},
	"Vehicle Document": {
		"on_update": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
		"after_delete": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
	},
	"Vehicle Availability Block": {
		"on_update": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
		"on_submit": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
		"on_cancel": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
	},
	"Vehicle Incident": {
		"on_update": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
		"on_submit": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
		"on_cancel": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
		"after_delete": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
	},
}

doctype_js = {
	"Address": "public/js/address.js",
	"Delivery Note": "public/js/delivery_note.js",
	"Delivery Trip": "public/js/delivery_trip.js",
	"Quality Inspection": "public/js/quality_inspection.js",
	"Sales Order": "public/js/sales_order.js",
	"Vehicle": "public/js/vehicle.js",
	"Vehicle Incident": "public/js/vehicle_incident_location.js",
}

doctype_list_js = {
	"Delivery Note": "public/js/delivery_note_list.js",
}

override_doctype_dashboards = {
	"Delivery Note": "vuna_transport.overrides.delivery_note_dashboard.get_data",
	"Delivery Trip": "vuna_transport.overrides.delivery_trip_dashboard.get_data",
	"Sales Order": "vuna_transport.overrides.sales_order_dashboard.get_data",
	"Vehicle": "vuna_transport.overrides.vehicle_dashboard.get_data",
}

fixtures = [
	{
		"dt": "Role",
		"filters": {
			"name": [
				"in",
				["Transport Planner", "Transport Dispatcher", "Transport Manager"],
			]
		},
	}
]

after_migrate = [
	"vuna_transport.install.ensure_transport_uoms",
	"vuna_transport.install.normalize_transport_cost_natures",
	"vuna_transport.install.ensure_dispatch_scan_roles",
	"vuna_transport.services.routing.location_service.backfill_address_coordinates",
	"vuna_transport.services.fleet.fleet_service.sync_vehicle_operational_snapshots",
]

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

scheduler_events = {
	"hourly": [
		"vuna_transport.services.fleet.fleet_service.sync_vehicle_operational_snapshots",
	],
	# "all": [
	# 	"vuna_transport.tasks.all"
	# ],
	# "daily": [
	# 	"vuna_transport.tasks.daily"
	# ],
	# "weekly": [
	# 	"vuna_transport.tasks.weekly"
	# ],
	# "monthly": [
	# 	"vuna_transport.tasks.monthly"
	# ],
}

# Testing
# -------

before_tests = "vuna_transport.setup.utils.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "vuna_transport.custom.task.CustomTaskMixin"
# }
extend_doctype_class = {
	"Quality Inspection": ["vuna_transport.overrides.quality_inspection.ReturnQualityInspectionMixin"],
}

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "vuna_transport.event.get_events"
# }
override_whitelisted_methods = {
	"erpnext.controllers.stock_controller.check_item_quality_inspection": "vuna_transport.overrides.quality_inspection.check_item_quality_inspection",
	"erpnext.controllers.stock_controller.make_quality_inspections": "vuna_transport.overrides.quality_inspection.make_quality_inspections",
}
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "vuna_transport.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["vuna_transport.utils.before_request"]
# after_request = ["vuna_transport.utils.after_request"]

# Job Events
# ----------
# before_job = ["vuna_transport.utils.before_job"]
# after_job = ["vuna_transport.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"vuna_transport.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
