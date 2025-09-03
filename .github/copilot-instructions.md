# Shopee Bridge — Copilot Instruction

You are generating a brand new ERPNext app named **shopee_bridge** that integrates Shopee Open Platform v2. 
Your output must be production-ready skeletons with clear separation of concerns, idempotent writes, 
and minimal side effects. Write clean, typed Python where possible and add rich docstrings.

## Architecture rules
- App name: `shopee_bridge`
- Python package layout must be:
  shopee_bridge/
    api.py                     # thin façade, whitelisted ERPNext endpoints
    auth.py                    # OAuth v2, token store, HMAC signing, webhook verification
    clients.py                 # signed HTTP GET/POST wrappers + 401 rotate
    mappers.py                 # pure mappers: Shopee → ERPNext rows (no frappe writes)
    services/
      orders.py                # order pull/upsert + SO/SI/DN "ensure_*" functions
      logistics.py             # shipping status + label attach
      returns.py               # returns/refunds upsert + CN/SR creation
      finance.py               # escrow fees patch + bank transaction + reconcile
      fiscal.py                # full-year backfill orchestrator
      webhook_handlers.py      # handle_order_push/return/logistics (idempotent)
    jobs/
      sync_orders.py           # cron incremental pulls (10m)
      sync_shipping.py
      sync_returns.py
      sync_finance.py          # hourly escrow batch
      process_webhook.py       # dispatcher + retry_due
      reconcile_bank.py
      backfill_fy.py
    shopee_bridge/doctype/
      shopee_settings/
        shopee_settings.json   # Single doctype schema
        shopee_settings.py     # server methods (connect/test/webhooks buttons)
      shopee_webhook_inbox/
        shopee_webhook_inbox.json
        shopee_webhook_inbox.py
      shopee_sync_log/
        shopee_sync_log.json
        shopee_sync_log.py
    setup/install.py           # after_install → create Custom Fields & defaults
    patches.txt
    patches/add_custom_fields.py
    hooks.py
    config/desktop.py
    config/docs.py
    docs/workflow.md           # functional workflow (already provided by user)

- Do not put business logic in `api.py`. It must only validate input, call services/jobs and return JSON.
- Each "ensure_*" function must be **idempotent**. Keys:
  - Sales Order / Sales Invoice / Delivery Note: `shopee_order_sn`
  - Return/Issue: `return_sn`
  - Logistics: `package_number` or `tracking_number`
  - Escrow patch: `(order_sn, payout_batch_id)`
  - Webhook event: `event_id` if present else `sha1("{event_type}:{entity}:{status}:{update_time}")`
- Never downgrade statuses (anti-regression). Compare `update_time` from Shopee against stored `last_pushed_update_time`.
- Always catch and log errors into **Shopee Sync Log** and **Shopee Webhook Inbox**.

## Shopee API endpoints (v2)
BASE (prod): https://partner.shopeemobile.com
BASE (test): https://partner.test-stable.shopeemobile.com

OAuth:
- GET  /api/v2/shop/auth_partner
- POST /api/v2/auth/token/get
- POST /api/v2/auth/token/refresh

Orders:
- GET  /api/v2/order/get_order_list
- GET  /api/v2/order/get_order_detail

Finance:
- GET  /api/v2/payment/get_escrow_detail

Logistics:
- GET  /api/v2/logistics/get_channel_list
- GET  /api/v2/logistics/get_shipping_parameter
- POST /api/v2/logistics/ship_order
- GET  /api/v2/logistics/get_tracking_number
- GET  /api/v2/logistics/get_shipping_document_parameter
- GET  /api/v2/logistics/get_shipping_document
- GET  /api/v2/logistics/download_shipping_document

Products/Media:
- POST /api/v2/media_space/upload_image
- POST /api/v2/media_space/init_video_upload
- POST /api/v2/media_space/upload_video_part
- POST /api/v2/media_space/complete_video_upload
- GET  /api/v2/media_space/get_video_upload_result
- GET  /api/v2/product/get_category
- GET  /api/v2/product/get_attributes
- GET  /api/v2/product/get_brand_list
- GET  /api/v2/product/get_dts_limit
- POST /api/v2/product/init_tier_variation
- POST /api/v2/product/add_item
- POST /api/v2/product/update_size_chart

Returns:
- GET  /api/v2/returns/get_return_list
- GET  /api/v2/returns/get_return_detail
- GET  /api/v2/returns/get_available_solution
- POST /api/v2/returns/offer
- POST /api/v2/returns/accept_offer
- POST /api/v2/returns/dispute
- POST /api/v2/returns/upload_proof
- POST /api/v2/returns/confirm

Webhooks (Push Management):
- ERP endpoints to expose:
  - POST `/api/method/shopee_bridge.api.webhook_live`
  - POST `/api/method/shopee_bridge.api.webhook_test`
- Verify signature header (e.g., `X-Shopee-Signature`) using HMAC-SHA256 over **raw body** with push key (live/test).
- Validate timestamp drift ≤ 300s if timestamp header exists.
- Write an Inbox record with `status=queued` then quickly return 200. Do processing async.

## Doctypes & custom fields
- Shopee Settings (Single):
  partner_id, partner_key, region, redirect_url, shop_id,
  access_token, refresh_token, token_expires_at, scopes, oauth_state,
  live_partner_push_key, test_partner_push_key, webhook_live_enabled, webhook_test_enabled,
  shopee_bank_account, fee_account_uuid, voucher_account_uuid, delivery_protection_account_uuid, shipping_diff_account_uuid,
  last_auth_error
- Shopee Webhook Inbox (Standard):
  event_type, source_env, idempotency_key (Unique), signature_valid, status (queued|processing|done|failed|skipped),
  attempts, next_retry_at, payload_hash, payload_json, error_message, processed_at, creation
- Shopee Sync Log (Standard):
  job, key, status (ok|fail|skip), payload_hash, message, started_at, ended_at, meta_json
- Add Custom Fields to core doctypes:
  - Sales Order: shopee_order_sn (Unique, Index), buyer_user_id, shopee_sync_hash, last_pushed_update_time
  - Sales Invoice: shopee_order_sn (Unique, Index), escrow_synced (Check), escrow_synced_at, escrow_fee_total, escrow_net, payout_batch_id, last_pushed_update_time
  - Delivery Note: shopee_order_sn, package_number, tracking_number (Index), status_pickup, status_delivery, delivered_at
  - Customer Issue (optional): return_sn (Unique, Index), shopee_payload_json

## Required functions per file (stubs to generate)

auth.py
- build_authorize_url(scopes: list[str]) -> str
- handle_oauth_callback(params: dict) -> None
- exchange_code_for_token(code: str, shop_id: str|int) -> dict
- refresh_if_needed(buffer_seconds: int = 600) -> bool
- refresh_token_via_api() -> dict
- sign_request(path: str, params: dict, body: bytes|str|None) -> dict
- verify_webhook_signature(path: str, raw_body: bytes, headers: dict, push_key: str) -> bool
- schedule_token_renewal_cron() -> dict

clients.py
- http_get(path: str, params: dict) -> dict
- http_post(path: str, json: dict|None = None, files: dict|None = None) -> dict
- rotate_on_401(send_callable: callable) -> dict

services/orders.py
- get_order_list(time_from: int, time_to: int, status: str|None, page_size: int = 100) -> list[str]
- get_order_detail(order_sn_list: list[str]) -> list[dict]
- ensure_customer_and_addresses(order: dict) -> tuple[str, str]
- upsert_sales_order(order: dict) -> str
- ensure_sales_invoice_for_paid(so_name: str, order: dict) -> str
- ensure_delivery_note_for_ready(so_or_si: str, order: dict) -> str
- on_completed(order_sn: str) -> None
- sync_incremental_orders(updated_since_minutes: int = 15) -> dict

services/logistics.py
- get_shipping_parameter(order_sn: str) -> dict
- ship_order(order_sn: str, method: str, params: dict) -> dict
- get_tracking_number(order_sn: str) -> str
- get_shipping_document_parameter(order_sn: str) -> dict
- get_shipping_document(order_sn: str) -> dict
- download_shipping_document(doc_id: str) -> bytes
- attach_shipping_label(dn_name: str, pdf_bytes: bytes, filename: str) -> None
- update_tracking_status(dn_name: str, status_payload: dict) -> bool
- sync_shipping_status(updated_since_minutes: int = 30) -> dict

services/returns.py
- get_return_list(time_from: int, time_to: int, status: str|None) -> list[str]
- get_return_detail(return_sn: str) -> dict
- get_available_solution(return_sn: str) -> list[dict]
- offer_solution(return_sn: str, solution: dict) -> dict
- accept_offer(return_sn: str) -> dict
- raise_dispute(return_sn: str, reason: str) -> dict
- upload_proof(return_sn: str, files: list[bytes]) -> dict
- confirm_return(return_sn: str) -> dict
- upsert_customer_issue_from_return(payload: dict) -> str
- create_sales_return_or_credit_note(issue_name: str) -> str
- close_return_case(issue_name: str) -> None
- sync_returns_incremental(updated_since_minutes: int = 30) -> dict

services/finance.py
- get_escrow_detail(order_sn: str) -> dict
- patch_invoice_with_fees(escrow: dict) -> str
- ensure_bank_transaction_from_escrow(escrow: dict) -> str
- sync_escrow_for_order(order_sn: str) -> dict
- sync_escrow_for_completed_orders(min_age_hours: int = 3, limit: int = 200) -> dict
- reconcile_bank_strict(days_back: int = 2) -> dict
- finance_backfill_range(start: str, end: str) -> dict

services/fiscal.py
- run_fiscal_year_full_sync(company: str, fiscal_year_name: str) -> dict
- backfill_orders_for_range(start: str, end: str, chunk_days: int = 7) -> dict
- backfill_returns_for_range(start: str, end: str, chunk_days: int = 7) -> dict
- backfill_shipping_for_range(start: str, end: str, chunk_days: int = 7) -> dict
- backfill_finance_for_range(start: str, end: str, min_age_hours: int = 3, chunk_days: int = 7) -> dict
- reconcile_bank_for_range(start: str, end: str) -> dict
- generate_integrity_report(start: str, end: str) -> str

services/webhook_handlers.py
- handle_order_push(event: dict, env: str) -> None
- handle_return_push(event: dict, env: str) -> None
- handle_logistics_push(event: dict, env: str) -> None

jobs/process_webhook.py
- run(inbox: str) -> None
- retry_due() -> dict
- derive_idempotency_key(event: dict) -> str

api.py (whitelisted)
- connect_to_shopee(scopes: list[str]) -> str
- oauth_callback(**params) -> dict
- test_shopee_connection() -> dict
- webhook_live() -> dict
- webhook_test() -> dict
- sync_orders_api(minutes: int = 15) -> dict
- sync_finance_api() -> dict

## Acceptance Criteria (each file)
- All functions exist with complete docstrings (purpose, params, returns, idempotency notes, raises).
- No business logic in api.py; services & jobs are imported and called.
- Webhook endpoints: insert Shopee Webhook Inbox row, enqueue job, return 200 quickly.
- Every ERP write uses deterministic keys & "ensure_*" patterns to be idempotent.
- Logging: create helper to write Shopee Sync Log records with payload hash SHA1.
- Add TODO markers where implementation specifics are needed (mapping, field names).
