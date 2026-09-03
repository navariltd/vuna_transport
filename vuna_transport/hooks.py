app_name = "vuna_transport"
app_title = "Vuna Transport"
app_publisher = "Yovuna Technologies Limited"
app_description = "Transport Management"
app_email = "solutions@navari.co.ke"
app_license = "agpl-3.0"

# Apps
# ------------------

required_apps = ["frappe/erpnext"]

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
# jinja = {
# 	"methods": "vuna_transport.utils.jinja_methods",
# 	"filters": "vuna_transport.utils.jinja_filters"
# }

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
	"Address": {
		"validate": "vuna_transport.services.routing.location_service.synchronize_address_location",
	},
	"Sales Order": {
		"on_submit": "vuna_transport.services.demand.delivery_order_service.create_delivery_orders_for_sales_order",
		"on_update_after_submit": "vuna_transport.services.demand.delivery_order_service.sync_delivery_orders_for_sales_order",
	},
	"Delivery Note": {
		"validate": "vuna_transport.services.execution.lineage_service.populate_delivery_note_delivery_links",
		"on_submit": "vuna_transport.services.execution.lineage_service.complete_load_without_trip",
		"on_cancel": "vuna_transport.services.execution.lineage_service.reconcile_load_after_delivery_note_cancel",
	},
	"Delivery Trip": {
		"validate": "vuna_transport.services.execution.lineage_service.validate_delivery_trip",
		"on_update": [
			"vuna_transport.services.execution.lineage_service.link_delivery_trip",
			"vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
		],
		"on_submit": [
			"vuna_transport.services.execution.lineage_service.submit_delivery_trip",
			"vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
		],
		"on_cancel": [
			"vuna_transport.services.execution.lineage_service.cancel_delivery_trip",
			"vuna_transport.services.execution.lineage_service.unlink_delivery_trip",
			"vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
		],
		"on_trash": [
			"vuna_transport.services.execution.lineage_service.unlink_delivery_trip",
		],
		"after_delete": "vuna_transport.services.fleet.fleet_service.sync_related_vehicle_snapshots",
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
	"Delivery Load": {
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
	"Delivery Trip": "public/js/delivery_trip.js",
	"Sales Order": "public/js/sales_order.js",
	"Vehicle": "public/js/vehicle.js",
	"Vehicle Incident": "public/js/vehicle_incident_location.js",
}

override_doctype_dashboards = {
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

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "vuna_transport.event.get_events"
# }
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
