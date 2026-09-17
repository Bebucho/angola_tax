app_name = "angola_tax"
app_title = "Angola Tax"
app_publisher = "Metagest"
app_description = "Angola tax compliance and fiscal obligation control"
app_email = "jose.sardinha@2ms.co.ao"
app_license = "gpl-3.0"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "angola_tax",
# 		"logo": "/assets/angola_tax/logo.png",
# 		"title": "Angola Tax",
# 		"route": "/angola_tax",
# 		"has_permission": "angola_tax.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/angola_tax/css/angola_tax.css"
# app_include_js = "/assets/angola_tax/js/angola_tax.js"

# include js, css files in header of web template
# web_include_css = "/assets/angola_tax/css/angola_tax.css"
# web_include_js = "/assets/angola_tax/js/angola_tax.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "angola_tax/public/scss/website"

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
# app_include_icons = "angola_tax/public/icons.svg"

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

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "angola_tax.utils.jinja_methods",
# 	"filters": "angola_tax.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "angola_tax.install.before_install"
# after_install = "angola_tax.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "angola_tax.uninstall.before_uninstall"
# after_uninstall = "angola_tax.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "angola_tax.utils.before_app_install"
# after_app_install = "angola_tax.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "angola_tax.utils.before_app_uninstall"
# after_app_uninstall = "angola_tax.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "angola_tax.notifications.get_notification_config"

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

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"angola_tax.tasks.all"
# 	],
# 	"daily": [
# 		"angola_tax.tasks.daily"
# 	],
# 	"hourly": [
# 		"angola_tax.tasks.hourly"
# 	],
# 	"weekly": [
# 		"angola_tax.tasks.weekly"
# 	],
# 	"monthly": [
# 		"angola_tax.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "angola_tax.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "angola_tax.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "angola_tax.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["angola_tax.utils.before_request"]
# after_request = ["angola_tax.utils.after_request"]

# Job Events
# ----------
# before_job = ["angola_tax.utils.before_job"]
# after_job = ["angola_tax.utils.after_job"]

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
# 	"angola_tax.auth.validate"
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

