# Shopee Bridge Implementation Strategy with Claude Code

## 🎯 Implementation Approach

### Phase 1: Foundation & Structure
```bash
cd ~/frappe-bench/apps/shopee_bridge
claude
```

**Prompt 1: Project Foundation**
```
I need to implement a complete Shopee Bridge ERPNext app based on detailed specifications. This is a production e-commerce integration with Shopee's v2 API.

Key requirements:
- Clean architecture with separation of concerns
- Idempotent operations (ensure_* patterns)
- Comprehensive error handling and logging
- OAuth2 authentication with token refresh
- Webhook processing with signature verification
- Complete order lifecycle management

Please start by:
1. Analyzing the current project structure
2. Creating the core foundation files (hooks.py, pyproject.toml, README.md)
3. Setting up the proper package structure as specified
4. Creating the base configuration and setup files

Focus on clean, typed Python with rich docstrings. Make everything production-ready.

[Paste the full specification document here]
```

### Phase 2: Core Infrastructure
**Prompt 2: Authentication & HTTP Clients**
```
Now implement the core infrastructure:

1. **auth.py** - Complete OAuth2 flow with Shopee
   - build_authorize_url() for OAuth initiation
   - handle_oauth_callback() for code exchange
   - refresh_if_needed() with automatic token rotation
   - sign_request() for API authentication
   - verify_webhook_signature() for security

2. **clients.py** - Robust HTTP client with retry logic
   - http_get() and http_post() with proper error handling
   - rotate_on_401() for automatic token refresh
   - Rate limiting and timeout handling

Requirements:
- Use Python type hints throughout
- Implement proper error handling with custom exceptions
- Add comprehensive logging
- Make all operations idempotent
- Follow the exact function signatures from the spec
- semua pake time epoch utc gausah pake datetime,
- cron job saat abis refresh if needed aja gausah pake fungsi lain

Test the OAuth flow and basic API connectivity.
```

### Phase 3: DocTypes & Database Layer
**Prompt 3: DocTypes and Custom Fields**
```
Create the complete database layer:

1. **DocTypes** (in shopee_bridge/doctype/):
   - shopee_settings/ (Single doctype)
   - shopee_webhook_inbox/ (Standard doctype)
   - shopee_sync_log/ (Standard doctype)
   - customer_issue/ (Optional extended returns)

2. **Custom Fields** for core ERPNext doctypes:
   - Sales Order: custom_order_sn, so.po_no, buyer_user_id, etc.
   - Sales Invoice: escrow fields, sync tracking
   - Delivery Note: logistics tracking fields

3. **Bootstrap Patch** (patches/0001_bootstrap.py):
   - Create all custom fields idempotently
   - Set up module registration properly
   - Create workspace with shortcuts
   - Initialize Shopee Settings

Make sure the module registration works flawlessly - no more "Module not found" errors!
```

### Phase 4: Business Logic Services
**Prompt 4: Order Management Service**
```
Implement the complete order management service (services/orders.py):

Key functions:
- get_order_list() and get_order_detail() for Shopee API calls
- ensure_customer_and_addresses() for customer creation
- upsert_sales_order() with idempotent writes
- ensure_sales_invoice_for_paid() for payment processing
- ensure_delivery_note_for_ready() for fulfillment
- sync_incremental_orders() for scheduled syncing

Critical requirements:
- All operations must be idempotent using custom_shopee_order_sn, so.po_no as key
- Never downgrade order status (check last_pushed_update_time)
- Comprehensive error handling with Shopee Sync Log entries
- Proper mapping from Shopee data to ERPNext fields

Focus on robust data transformation and error recovery.
```

**Prompt 5: Logistics & Shipping Service**
```
Implement shipping and logistics management (services/logistics.py):

Features:
- get_shipping_parameter() and ship_order() for order fulfillment
- get_tracking_number() and shipping document handling
- download_shipping_document() with file attachment
- update_tracking_status() for delivery updates
- sync_shipping_status() for scheduled updates

Requirements:
- Attach shipping labels to Delivery Notes
- Track package_number and tracking_number
- Handle multiple shipping providers
- Update delivery status in real-time
- Proper error handling for shipping failures
- selalu update doctype terkait untuk implementasi fungsinya
```

**Prompt 6: Returns & Finance Services**
```
Implement returns and finance management:

1. **services/returns.py**:
   - Complete return workflow management
   - Customer issue tracking and resolution
   - Integration with Sales Return/Credit Note

2. **services/finance.py**:
   - Escrow detail processing
   - Invoice fee patching
   - Bank transaction reconciliation
   - Automated financial reporting

Both services need idempotent operations and comprehensive audit trails.
```

### Phase 5: Webhook & Job Processing
**Prompt 7: Webhook Infrastructure**
```
Implement the complete webhook and job processing system:

1. **services/webhook_handlers.py**:
   - handle_order_push(), handle_return_push(), handle_logistics_push()
   - Idempotent event processing with deduplication

2. **jobs/process_webhook.py**:
   - Async webhook processing from inbox
   - Retry logic with exponential backoff
   - Dead letter queue handling

3. **All scheduled jobs**:
   - sync_orders.py (10-minute incremental)
   - sync_shipping.py (30-minute status updates)  
   - sync_finance.py (hourly escrow processing)
   - reconcile_bank.py (daily reconciliation)

Ensure webhook signature verification and proper error handling.
```

### Phase 5b: Jobs Implementation
**Prompt 7b: Implement All Jobs in jobs/***
```
Implement all job modules in the jobs/ folder:

1. **process_webhook.py**
   - Dispatcher for Shopee Webhook Inbox
   - Async event processing, retry_due logic, idempotency key derivation
   - Error logging and status updates

2. **sync_finance.py**
   - Hourly batch sync of Shopee escrow and finance data
   - Patch invoices, reconcile bank transactions, log results

3. **sync_orders.py**
   - Incremental order pull (every 10 minutes)
   - Upsert Sales Orders, Sales Invoices, Delivery Notes
   - Idempotent writes, anti-regression status logic

4. **sync_returns.py**
   - Scheduled returns sync (every 30 minutes)
   - Upsert Customer Issues, Sales Returns, Credit Notes
   - Error handling and logging

5. **sync_shipping.py**
   - Scheduled shipping status refresh (every 30 minutes)
   - Update Delivery Notes, attach shipping labels, track logistics

6. **backfill_fy.py**
   - Fiscal year backfill orchestrator
   - Chunked historical sync for orders, returns, shipping, finance

7. **reconcile_bank.py**
   - Strict bank reconciliation job
   - Match Shopee payouts to ERP bank transactions

Requirements:
- Each job must be idempotent and log results to Shopee Sync Log
- Use deterministic keys for all writes
- Catch and log all errors
- Add rich docstrings and type hints
- Follow the architecture and naming conventions
```

### Phase 6: API Layer & Integration
**Prompt 8: API Endpoints**
```
Create the clean API layer (api.py):

Whitelisted endpoints:
- connect_to_shopee() for OAuth initiation
- oauth_callback() for OAuth completion
- webhook_live() and webhook_test() for push events
- sync_orders_api(), sync_finance_api() for manual triggers
- test_shopee_connection() for health checks

Requirements:
- Thin façade pattern - no business logic here
- Proper input validation and sanitization
- Standardized JSON responses
- Rate limiting and security headers
- Complete API documentation
```

### Phase 7: Testing & Production Readiness
**Prompt 9: Testing & Documentation**
```
Make this production-ready:

1. **Comprehensive testing**:
   - Unit tests for all services
   - Integration tests for API workflows
   - Mock Shopee API responses for testing
   - Error scenario testing

2. **Documentation**:
   - Complete README with setup instructions
   - API documentation with examples
   - Troubleshooting guide
   - Deployment checklist

3. **Production features**:
   - Health check endpoints
   - Monitoring and alerting hooks
   - Performance optimization
   - Security hardening

4. **Developer experience**:
   - Setup automation scripts
   - Debug utilities
   - Log analysis tools
```

## 🚀 Execution Strategy

### Start with Claude Code:
```bash
cd ~/frappe-bench/apps/shopee_bridge
claude
```

### Use progressive prompts:
1. **Copy each prompt** from the phases above
2. **Let Claude Code implement** each phase completely
3. **Test each phase** before moving to the next
4. **Iterate and refine** based on results

### Key Success Factors:
- ✅ **Let Claude Code handle the heavy lifting** - file creation, implementation, testing
- ✅ **Provide clear, specific requirements** in each prompt
- ✅ **Test incrementally** - don't build everything at once
- ✅ **Focus on production quality** - error handling, logging, documentation
- ✅ **Make it maintainable** - clean code, good documentation

## 🎯 Expected Outcome

After completing all phases, you'll have:
- **Complete Shopee integration** with all major features
- **Production-ready code** with proper error handling
- **Clean architecture** that's easy to maintain and extend
- **Comprehensive testing** and documentation
- **Zero-config setup** that works out of the box

This approach leverages Claude Code's strengths in understanding complex requirements and generating complete, working implementations. Each phase builds on the previous one, ensuring a solid foundation throughout.