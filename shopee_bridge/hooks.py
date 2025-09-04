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
app_description = "Shopee ↔ ERPNext integration"
app_email = "akrilocreations@gmail.com"
app_version = "0.1.0"
app_license = "mit"

# Post-install bootstrap (creates custom fields, module, workspace, seed settings)
after_install = "shopee_bridge.setup.install.after_install"

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
        ],
        "*/5 * * * *": [
            "shopee_bridge.jobs.process_webhook.retry_due",
        ],
        "*/30 * * * *": [
            "shopee_bridge.auth.schedule_token_renewal_cron",
        ],
        "15 1 * * *": [
            "shopee_bridge.jobs.reconcile_bank.run",
        ],
    }
}

# Whitelisted method overrides (explicit mapping retained for clarity / future extension)
override_whitelisted_methods = {
    "shopee_bridge.api.webhook_live": "shopee_bridge.api.webhook_live",
    "shopee_bridge.api.webhook_test": "shopee_bridge.api.webhook_test",
}

# Optional fixtures placeholder (enable only if needed to ship static Custom Fields / Module Def)
# fixtures = ["Custom Field"]

