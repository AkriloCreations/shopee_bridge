# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Shopee Bridge** is a production-ready ERPNext app that integrates Shopee Open Platform v2 with ERPNext. It handles OAuth authentication, order synchronization, logistics management, returns processing, and financial reconciliation with a self-healing architecture.

## Development Commands

### Testing
```bash
# Run basic functionality tests
cd apps/shopee_bridge
python tests/test_basic.py

# Run specific test modules
python tests/test_cli.py
python tests/test_finance.py
python tests/test_helpers.py
```

### CLI Operations
```bash
# Audit recent orders (from bench directory)
cd apps/shopee_bridge
python scripts/cli.py audit --days 7

# Sync recent orders
python scripts/cli.py sync --minutes 30

# Check system health
python scripts/cli.py health

# Debug webhook payload
python scripts/cli.py debug-webhook WEBHOOK_INBOX_NAME
```

### Code Quality Tools
```bash
# Install pre-commit hooks
pre-commit install

# Run pre-commit manually on all files
pre-commit run --all-files

# The following tools are configured via pre-commit:
# - ruff (linting and formatting)
# - eslint (JavaScript linting) 
# - prettier (JavaScript/CSS formatting)
```

### Linting and Formatting
```bash
# Run ruff linter
ruff check .

# Run ruff formatter
ruff format .

# Ruff configuration is in pyproject.toml with line-length=110, target-version=py310
```

## Architecture Overview

This is a **Frappe v15** app with modern architecture emphasizing:
- **Zero Configuration**: Works immediately after installation
- **Self-Healing**: Automatically repairs common issues
- **Idempotent Operations**: All sync operations can be safely repeated

### Directory Structure
```
shopee_bridge/
├── shopee_bridge/                    # Main app package
│   ├── hooks.py                     # Frappe hooks and schedulers
│   ├── api.py                       # Whitelisted API endpoints
│   ├── auth.py                      # OAuth v2 + token management
│   ├── clients.py                   # HTTP client wrappers
│   ├── mappers.py                   # Data transformation functions
│   ├── services/                    # Business logic modules
│   │   ├── orders.py               # Order sync and processing
│   │   ├── logistics.py            # Shipping management
│   │   ├── returns.py              # Returns/refunds handling
│   │   ├── finance.py              # Escrow and bank reconciliation
│   │   └── webhook_handlers.py     # Push event processors
│   ├── jobs/                       # Scheduled background tasks
│   │   ├── sync_orders.py          # Cron order sync
│   │   ├── sync_shipping.py        # Shipping status updates
│   │   ├── process_webhook.py      # Webhook processing
│   │   └── reconcile_bank.py       # Bank reconciliation
│   ├── setup/                      # Installation system
│   └── patches/                    # Database migrations
├── scripts/                        # CLI tools for development
├── tests/                          # Unit tests
└── docs/                          # Documentation
```

### Key DocTypes
- **Shopee Settings** (Single): OAuth tokens, API keys, configuration
- **Shopee Webhook Inbox**: Incoming webhook events queue  
- **Shopee Sync Log**: Operation logging and audit trails
- **Custom Fields** added to: Sales Order, Sales Invoice, Delivery Note, Customer Issue

### Scheduled Jobs (Cron)
- Every 10 minutes: Order, shipping, returns sync
- Every hour: Finance sync, webhook processing
- Every 5 minutes: Webhook retry processing  
- Every 30 minutes: Token refresh check
- Daily: Bank reconciliation

## API Integration Details

**Base URLs:**
- Production: `https://partner.shopeemobile.com`
- Test: `https://partner.test-stable.shopeemobile.com`

**Authentication:** OAuth 2.0 with automatic token refresh

**Key Endpoints Used:**
- Orders: `/api/v2/order/get_order_list`, `/api/v2/order/get_order_detail`
- Finance: `/api/v2/payment/get_escrow_detail`
- Logistics: `/api/v2/logistics/*` (shipping, tracking, documents)
- Returns: `/api/v2/returns/*` (returns processing)

## Development Guidelines

### Idempotency Requirements
All sync operations MUST be idempotent using these keys:
- Sales Order/Invoice/Delivery Note: `shopee_order_sn`
- Returns: `return_sn`
- Logistics: `package_number` or `tracking_number`
- Webhook events: `event_id` or computed hash

### Error Handling
- Always log errors to **Shopee Sync Log**
- Webhook errors go to **Shopee Webhook Inbox**
- Never downgrade statuses (anti-regression)
- Compare `update_time` from Shopee against stored `last_pushed_update_time`

### Code Style
- Python 3.10+ with type hints where possible
- Line length: 110 characters
- Use tabs for indentation
- Follow ruff configuration in `pyproject.toml`

### Adding New Features
1. Add business logic to appropriate `services/` module
2. Create scheduled job in `jobs/` if needed
3. Add whitelisted API endpoint in `api.py`
4. Update `hooks.py` scheduler or whitelist as needed
5. Add tests in `tests/`
6. Document in relevant `.md` files

### Webhook Handling
- Verify HMAC-SHA256 signatures using raw request body
- Write to Shopee Webhook Inbox immediately, return 200
- Process asynchronously via background jobs
- Use `event_id` for deduplication

## Important Implementation Notes

- **No business logic in `api.py`** - it only validates input and calls services
- **All `ensure_*` functions must be idempotent**
- **OAuth tokens refresh automatically** via scheduler
- **Self-healing bootstrap system** repairs common installation issues
- **Comprehensive logging** for all operations and errors