# Shopee Bridge – Architecture & Functional Workflow

> Status: EARLY STUB / SAFE MODE (many create/update operations are placeholders to allow wiring & testing without side‑effects).

## 1. High Level Flow
1. Install app → `after_install` creates custom fields (incl. `buyer_username`), Module Def, Workspace, seeds single `Shopee Settings` doc.
2. User initiates OAuth connect (`connect_to_shopee`) → redirected to Shopee → callback (`oauth_callback`) stores tokens (stub persistence for now).
3. Background jobs (manual or scheduled) call service layer: orders, returns, logistics, finance.
4. Shopee sends push events → `/api/method/...webhook_live` validates HMAC signature & timestamp → inserts Webhook Inbox row (payload JSON + idempotency key) → enqueues dispatcher job.
5. Dispatcher job (`jobs.process_webhook.run`) routes to handler (order / returns / logistics) → service layer ensures / updates ERP docs (currently mocked) and anti‑regression via `last_pushed_update_time` when available.
6. Finance / escrow jobs fetch escrow detail (stub), patch invoice fees (stub), ensure bank transaction (stub), then (future) reconcile.
7. Backfill & fiscal utilities batch over date ranges (chunked) invoking the same incremental services for historical gaps.
8. Integrity report (stub) produces a private File summarizing placeholder metrics.

## 2. Module Responsibilities
| Module | File | Responsibility | Idempotency / Key Points |
|--------|------|----------------|--------------------------|
| Auth | `auth.py` | OAuth URL building, callback state validation, signing, webhook signature verify, token refresh helpers | State = random hash; HMAC signature over sorted params; webhook signature + 5‑min drift guard |
| HTTP Client | `clients.py` | Signed GET/POST, retry (429/5xx), heuristic 401 refresh | Retry delays 1s / 3s; SHA256 signing via `auth.sign_request` |
| Public API | `api.py` | Whitelisted endpoints (connect, callback, test, manual syncs, webhook ingest) | Webhook idempotency key derived before insert; skips duplicate payloads |
| Orders Service | `services/orders.py` | Pull order list/detail, ensure customer & addresses, upsert SO / SI / DN (stubs), status transitions | Customer naming prioritizes `buyer_username`; idempotent upserts keyed by `shopee_order_sn` |
| Logistics Service | `services/logistics.py` | Ship order, tracking number, label retrieval + attachment (idempotent hash) | Label SHA1 hash in File.description prevents re‑attach |
| Returns Service | `services/returns.py` | List, detail, offer/accept/dispute (stubs), incremental sync | Intended custom field `shopee_return_sn` for lookup |
| Finance Service | `services/finance.py` | Escrow detail, invoice fee patch, bank txn + reconciliation (all stubs) | Future custom fields: escrow_synced*, payout_batch_id, fee totals |
| Fiscal Utilities | `services/fiscal.py` | Chunked backfills and fiscal year orchestration | Pure orchestration; delegates to service stubs |
| Webhook Handlers | `services/webhook_handlers.py` | Thin push processors invoking services with anti‑regression check | Compares `update_time` to `last_pushed_update_time` |
| Webhook Dispatcher Job | `jobs/process_webhook.py` | Async processing & backoff scheduling | Backoff schedule [60,300,900,3600,10800]; retries until terminal |

## 3. Endpoints (Whitelisted)
| Endpoint (method) | Purpose | Notes |
|-------------------|---------|-------|
| `connect_to_shopee` | Start OAuth; returns redirect URL | Generates state & stores (TODO) |
| `oauth_callback` | Handle Shopee callback | Validates state, prepares token exchange payload |
| `test_shopee_connection` | Ping / identity test | Returns stub or live identity |
| `webhook_live` / `webhook_test` | Ingest signed webhook | Validates signature + timestamp, dedups by idempotency key |
| `sync_orders_api` | Manual incremental order sync | Calls orders.sync_incremental_orders |
| `sync_finance_api` | Manual escrow sync (stub) | Calls finance.sync_escrow_for_completed_orders |

## 4. Idempotency Strategies
| Domain | Strategy |
|--------|----------|
| Webhook Inbox | SHA1 hash of composite or `event_id` → unique index (planned) prevents duplicates |
| Orders | `shopee_order_sn` custom field on Sales Order & downstream docs |
| Logistics Labels | SHA1(pdf_bytes) stored in File.description; skip if already attached |
| Returns | (Planned) `shopee_return_sn` on Issue / Return DocType |
| Finance Fees | (Planned) Combination (order_sn, payout_batch_id) on Sales Invoice lines |
| Bank Transactions | (Planned) (reference, amount) uniqueness |

## 5. Backoff & Retry
Webhook processing backoff steps (seconds): 60 → 300 → 900 → 3600 → 10800. After final interval, tries remain using last delay unless future policy enforces terminal cutoff.

`retry_due()` scans failed inbox rows with `next_retry_at <= now` and re‑enqueues. (Bug fix: corrected filter syntax to ensure proper comparison.)

## 6. Security & Validation
| Aspect | Implementation | Future Enhancements |
|--------|----------------|---------------------|
| OAuth State | Random hex stored server side (TODO persistence) | Expiry & single‑use enforcement |
| Request Signing | Partner/signature via HMAC-SHA256 | Central cache for partner credentials |
| Webhook Auth | HMAC header + timestamp drift (<=300s) | Replay cache to block duplicates |
| Data Mutation | Mostly disabled (stubs) | Enable progressively with tests |

## 7. Key Custom Fields (Created in `after_install`)
| DocType | Field | Purpose |
|---------|-------|---------|
| Sales Order | shopee_order_sn (Data, unique) | External identity / idempotent upsert |
| Sales Order | buyer_username (Data) | Customer naming & traceability |
| Customer | shopee_buyer_username (Data) | Reverse lookup, future dedupe |
| (Planned) Sales Invoice | escrow_* fields | Escrow sync markers |
| (Planned) Issue / Return | shopee_return_sn | Return linkage |

## 8. Persistence Gaps (TODO)
| Area | Current | Needed Before Prod |
|------|---------|-------------------|
| Token Storage | In-memory / placeholder | Encrypted doctype or key store + refresh job |
| Order Upserts | Mock names only | Real SO/SI/DN creation w/ transaction boundaries |
| Returns Integration | Mock Issue name | Issue/Return DocType + status mapping |
| Finance Escrow Patch | Mock invoice name | Actual fee line insertion & net calculation |
| Bank Reconciliation | Mock metrics | Strict matching + audit trail |
| Replay Protection | None | Cache or DB table of processed webhook signature + ts |

## 9. Job & Scheduling Overview
| Job | Function | Trigger |
|-----|----------|---------|
| Webhook Processing | `jobs.process_webhook.run` | Enqueued per inbox row |
| Webhook Retry Scanner | `jobs.process_webhook.retry_due` | Cron every 1–5 min |
| Incremental Orders | (future scheduler) | Cron (e.g., every 10 min) |
| Incremental Returns | (future scheduler) | Cron |
| Shipping Status | `logistics.sync_shipping_status` | Cron |
| Escrow Sync | `finance.sync_escrow_for_completed_orders` | Cron hourly |
| Token Refresh | `auth.cron_refresh_job` | Cron (e.g., every 30 min) |
| Fiscal Year Full Sync | `fiscal.run_fiscal_year_full_sync` | Manual / on demand |

## 10. Data Flow (Order Example)
Shopee API → (pull) orders.list/detail → map fields → ensure Customer (by buyer_username) → upsert Sales Order (idempotent) → on status change ensure Sales Invoice / Delivery Note → on completion mark & later finance escrow patch.

## 11. Troubleshooting
| Symptom | Likely Cause | Action |
|---------|--------------|--------|
| Module not visible / "Module Shopee Bridge not found" | Module Def missing or cache not cleared | Confirm Module Def exists; run `bench clear-cache && bench migrate`; ensure `modules.txt` contains `Shopee Bridge` |
| Webhook always skipped | Unknown `event_type` string | Verify Shopee event name; add routing branch |
| Duplicate label attachments | Hash mismatch (file changed) | Expected if actual bytes differ; inspect File.description |
| No retries happening | Filter bug (fixed) or scheduler inactive | Ensure `retry_due` scheduled; check failed rows have `next_retry_at` in past |

## 12. Next Implementation Steps
1. Persist tokens + refresh logic.
2. Real Sales Order / Invoice / Delivery Note creation with mapping tests.
3. Returns Issue DocType & credit note flow.
4. Escrow fee patch + bank transaction & reconciliation.
5. Replay protection cache for webhooks.
6. Enable strict idempotent constraints (unique indexes) once stable.
7. Flesh out integrity report metrics & hash digest.

---
This document will evolve as stubbed areas gain full implementations.
