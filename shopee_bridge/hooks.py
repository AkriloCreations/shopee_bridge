# -*- coding: utf-8 -*-
app_name = "shopee_bridge"
app_title = "Shopee Bridge"
app_publisher = "Your Team"
app_description = "Shopee ↔ ERPNext integration"
app_email = "dev@example.com"
app_version = "0.1.0"
app_license = "mit"

# Jalankan script setelah install (opsional, boleh kamu aktifkan nanti)
after_install = "shopee_bridge.setup.install.after_install"

# Jadwal (bisa kosong dulu biar simple)
scheduler_events = {
    # "all": [],
    # "daily": [],
    # "hourly": [],
    "cron": {
        # Contoh (aktifkan nanti bila job-nya sudah ada):
        # "*/10 * * * *": [
        #     "shopee_bridge.jobs.sync_orders.run",
        #     "shopee_bridge.jobs.sync_shipping.run",
        #     "shopee_bridge.jobs.sync_returns.run",
        # ],
        # "0 * * * *": [
        #     "shopee_bridge.jobs.sync_finance.run",
        # ],
        # "*/5 * * * *": [
        #     "shopee_bridge.jobs.process_webhook.retry_due",
        # ],
        # "*/30 * * * *": [
        #     "shopee_bridge.auth.schedule_token_renewal_cron",
        # ],
        # "15 1 * * *": [
        #     "shopee_bridge.jobs.reconcile_bank.run",
        # ],
    }
}

# Whitelisted endpoint override (boleh diisi nanti)
fixtures = [
    "Custom Field",
    "DocType"
]
override_whitelisted_methods = {
    # "shopee_bridge.api.webhook_live": "shopee_bridge.api.webhook_live",
    # "shopee_bridge.api.webhook_test": "shopee_bridge.api.webhook_test",
}
