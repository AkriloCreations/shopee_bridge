# Shopee Bridge Functional Workflow

1. User installs app and configures Shopee Settings.
2. User connects to Shopee via OAuth, tokens are stored.
3. Scheduler pulls orders, returns, shipping, finance incrementally.
4. Webhook endpoints receive push events, validate signature, insert inbox row, enqueue async job.
5. Orders are mapped and upserted to ERPNext Sales Order, Invoice, Delivery Note using idempotency keys.
6. Returns/refunds are mapped and upserted to Customer Issue, Sales Return, Credit Note.
7. Finance jobs patch invoices with escrow fees, create bank transactions, reconcile payouts.
8. Backfill jobs orchestrate full-year sync for orders, returns, shipping, finance.
9. All errors and events are logged to Shopee Sync Log and Shopee Webhook Inbox.
