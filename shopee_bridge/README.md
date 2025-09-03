# Shopee Bridge for ERPNext

ERPNext app for Shopee Open Platform v2 integration.

## Quickstart

1. Get app:
   ```bash
   bench get-app https://github.com/AkriloCreations/shopee_bridge.git
   bench install-app shopee_bridge
   ```
2. Configure Shopee Settings (partner_id, partner_key, region, etc).
3. Connect to Shopee (OAuth):
   - Click "Connect" in Shopee Settings.
   - Complete OAuth flow.
4. Run cron jobs manually:
   ```bash
   bench execute shopee_bridge.jobs.sync_orders.run
   bench execute shopee_bridge.jobs.sync_finance.run
   ```
5. Webhook endpoints:
   - POST `/api/method/shopee_bridge.api.webhook_live`
   - POST `/api/method/shopee_bridge.api.webhook_test`

See `docs/workflow.md` for full workflow.
