### Shopee Bridge

Shopee → ERPNext (OAuth + Orders)

## Quick Start

### Smoke Tests

Run basic functionality tests:

```bash
# From the bench directory
cd apps/shopee_bridge
python tests/test_basic.py
```

### CLI Operations

Use the CLI tool for quick operations:

```bash
# Audit recent orders
python scripts/cli.py audit --days 7

# Sync recent orders
python scripts/cli.py sync --minutes 30

# Check system health
python scripts/cli.py health

# Debug webhook payload
python scripts/cli.py debug-webhook WEBHOOK_INBOX_NAME
```

### API Endpoints

Key whitelisted endpoints for quick operations:

- `audit_orders` - Audit recent orders
- `sync_recent_orders` - Quick order sync
- `check_token_health` - Token status check
- `debug_webhook_payload` - Debug webhook issues
- `get_sync_logs` - View recent sync logs


### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/shopee_bridge
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### CI

This app can use GitHub Actions for CI. The following workflows are configured:

- CI: Installs this app and runs unit tests on every push to `develop` branch.
- Linters: Runs [Frappe Semgrep Rules](https://github.com/frappe/semgrep-rules) and [pip-audit](https://pypi.org/project/pip-audit/) on every pull request.


### License

mit
