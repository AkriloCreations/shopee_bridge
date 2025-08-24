app_name = "shopee_bridge"
app_title = "Shopee Bridge"
app_publisher = "Akrilo"
app_description = "Shopee → ERPNext bridge (OAuth, token refresh, orders to Sales Invoice + Payment Entry)"
app_email = "akrilocreations@gmail.com"
app_license = "MIT"
app_version = "0.0.1"
app_icon = "octicon octicon-sync"
app_color = "orange"

# Scheduler: jalan tiap 15 menit
scheduler_events = {
  "cron": {
    "*/15 * * * *": [
      "shopee_bridge.api.refresh_if_needed",
      "shopee_bridge.api.sync_recent_orders"
    ]
  }
}

website_route_rules = [
    {"from_route": "/app/oauth-callback", "to_route": "oauth_callback"},
]

app_include_js = [
    "shopee_bridge/js/oauth_handler.js"
]


