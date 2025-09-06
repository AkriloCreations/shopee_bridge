"""Frappe hooks for Shopee Bridge.

Keep this file minimal: only declarative metadata & schedules.
Avoid importing heavy modules (frappe / services) here to keep
bench start & migrate fast.

Future additions:
 - Add `fixtures` only if specific Custom Fields must be shipped.
 - Add `doc_events` when enabling document-level reactions.
 - Add `desktop_icons` / `app_include_js` when UI assets exist.
"""

# App metadata
app_name = "shopee_bridge"
app_title = "Shopee Bridge"
app_publisher = "Your Team"
app_description = "Modern Shopee ↔ ERPNext integration with smart self-healing"
app_email = "akrilocreations@gmail.com"
app_version = "2.0.0"
app_license = "mit"

# Modern post-install bootstrap (smart, self-healing system)
after_install = "shopee_bridge.setup.install_v2.after_install"

# JavaScript and CSS assets (handled directly in form JS)
# app_include_js = []

# Scheduler (cron based). Jobs are lightweight orchestrators; heavy logic lives in services.
scheduler_events = {
    "cron": {
        "*/10 * * * *": [
            "shopee_bridge.jobs.sync_orders.run",
            "shopee_bridge.jobs.sync_shipping.run",
            "shopee_bridge.jobs.sync_returns.run",
        ],
        "0 * * * *": [
            "shopee_bridge.jobs.sync_finance.run",
            "shopee_bridge.jobs.sync_finance.sync_recent_escrow",
            "shopee_bridge.jobs.process_webhook.process_pending"
        ],
        "*/5 * * * *": [
            "shopee_bridge.jobs.process_webhook.retry_due",
        ],
        "*/30 * * * *": [
            "shopee_bridge.auth.refresh_if_needed",
        ],
        "15 1 * * *": [
            "shopee_bridge.jobs.reconcile_bank.run",
        ],
    }
}

# Whitelisted method overrides - Complete API coverage
override_whitelisted_methods = {
    # Auth & Connection
    "shopee_bridge.api.connect_to_shopee": "shopee_bridge.api.connect_to_shopee",
    "shopee_bridge.api.oauth_callback": "shopee_bridge.api.oauth_callback", 
    "shopee_bridge.api.test_shopee_connection": "shopee_bridge.api.test_shopee_connection",
    "shopee_bridge.api.refresh_token": "shopee_bridge.api.refresh_token",
    
    # Webhooks
    "shopee_bridge.api.webhook_live": "shopee_bridge.api.webhook_live",
    "shopee_bridge.api.webhook_test": "shopee_bridge.api.webhook_test",
    "shopee_bridge.api.get_webhook_logs": "shopee_bridge.api.get_webhook_logs",
    "shopee_bridge.api.retry_webhook": "shopee_bridge.api.retry_webhook",
    
    # Orders
    "shopee_bridge.api.get_order": "shopee_bridge.api.get_order",
    "shopee_bridge.api.sync_order": "shopee_bridge.api.sync_order",
    "shopee_bridge.api.sync_orders_api": "shopee_bridge.api.sync_orders_api",
    "shopee_bridge.api.update_order_status": "shopee_bridge.api.update_order_status",
    
    # Logistics
    "shopee_bridge.api.get_shipping_info": "shopee_bridge.api.get_shipping_info",
    "shopee_bridge.api.sync_shipping_api": "shopee_bridge.api.sync_shipping_api",
    "shopee_bridge.api.update_tracking": "shopee_bridge.api.update_tracking",
    
    # Returns
    "shopee_bridge.api.get_returns": "shopee_bridge.api.get_returns",
    "shopee_bridge.api.sync_returns_api": "shopee_bridge.api.sync_returns_api", 
    "shopee_bridge.api.process_return": "shopee_bridge.api.process_return",
    
    # Finance
    "shopee_bridge.api.get_payout_info": "shopee_bridge.api.get_payout_info",
    "shopee_bridge.api.sync_finance_api": "shopee_bridge.api.sync_finance_api",
    "shopee_bridge.api.reconcile_bank_api": "shopee_bridge.api.reconcile_bank_api",
    "shopee_bridge.api.sync_escrow_batch": "shopee_bridge.api.sync_escrow_batch",
    
    # Utilities
    "shopee_bridge.api.get_health_status": "shopee_bridge.api.get_health_status",
}

# Optional fixtures placeholder (currently unused in v2.0 - dynamic system doesn't need fixtures)
fixtures = ["Workspace", "Module Def", "Number Card"]

