# hooks.py
from . import __version__ as app_version

app_name = "shopee_bridge"
app_title = "Shopee Bridge"
app_publisher = "AkriloCreations"
app_description = "Shopee integration for ERPNext"
app_email = "support@akrilocreations.com"
app_license = "MIT"

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/shopee_bridge/css/shopee_bridge.css"
app_include_js = [
    "shopee_bridge/js/oauth_handler.js"
]

# include js, css files in header of web template
# web_include_css = "/assets/shopee_bridge/css/shopee_bridge.css"
# web_include_js = "/assets/shopee_bridge/js/shopee_bridge.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "shopee_bridge/public/scss/website"

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
# 	"methods": "shopee_bridge.utils.jinja_methods",
# 	"filters": "shopee_bridge.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "shopee_bridge.install.before_install"
after_install = "shopee_bridge.setup.install.after_install"

# Uninstallation
# ---------------

# before_uninstall = "shopee_bridge.uninstall.before_uninstall"
# after_uninstall = "shopee_bridge.uninstall.after_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "shopee_bridge.notifications.get_notification_config"

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

scheduler_events = {
    # Every 15 minutes - Check token and sync recent orders
    "cron": {
        "*/15 * * * *": [
            "shopee_bridge.shopee_bridge.doctype.shopee_settings.api.scheduled_token_refresh",
            "shopee_bridge.shopee_bridge.doctype.shopee_settings.api.scheduled_order_sync"
        ],
        # Every hour - Full order sync
        "0 * * * *": [
            "shopee_bridge.shopee_bridge.doctype.shopee_settings.api.sync_recent_orders"
        ],
        # Every day at 2 AM - Sync items
        "0 2 * * *": [
            "shopee_bridge.shopee_bridge.doctype.shopee_settings.api.scheduled_item_sync"
        ]
    },
    # Alternative simpler format (choose one)
    # "all": [
    #     "shopee_bridge.tasks.all"
    # ],
    # "hourly": [
    #     "shopee_bridge.shopee_bridge.doctype.shopee_settings.api.scheduled_order_sync"
    # ],
    # "daily": [
    #     "shopee_bridge.shopee_bridge.doctype.shopee_settings.api.scheduled_item_sync"
    # ]
}

# Testing
# -------

# before_tests = "shopee_bridge.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "shopee_bridge.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "shopee_bridge.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["shopee_bridge.utils.before_request"]
# after_request = ["shopee_bridge.utils.after_request"]

# Job Events
# ----------
# before_job = ["shopee_bridge.utils.before_job"]
# after_job = ["shopee_bridge.utils.after_job"]

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
# 		"filter_by": "{filter_by}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"shopee_bridge.auth.validate"
# ]

# Website Route Rules
# -------------------

website_route_rules = [
    {"from_route": "/oauth-callback", "to_route": "oauth_callback"},
    {"from_route": "/shopee/oauth-callback", "to_route": "oauth_callback"},
    {"from_route": "/api/method/shopee_bridge.oauth_callback", "to_route": "oauth_callback"},
]

# Fixtures
# --------

fixtures = [
    {
        "doctype": "Custom Field",
        "filters": {
            "name": [
                "in",
                [
                    "Sales Invoice-shopee_order_sn",
                    "Customer-shopee_buyer_id", 
                    "Item-shopee_item_id",
                    "Item-shopee_model_id",
                    "Item-shopee_sku"
                ]
            ]
        }
    }
]