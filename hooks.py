app_name = "shopee_bridge"
app_title = "Shopee Bridge"
app_publisher = "Your Team"
app_description = "Shopee ↔ ERPNext bridge"
app_email = "dev@example.com"
app_version = "0.1.0"
app_license = "mit"

after_install = "shopee_bridge.setup.install.after_install"

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

override_whitelisted_methods = {
    "shopee_bridge.api.webhook_live": "shopee_bridge.api.webhook_live",
    "shopee_bridge.api.webhook_test": "shopee_bridge.api.webhook_test",
}
