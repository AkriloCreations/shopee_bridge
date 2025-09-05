# Shopee Bridge API Reference

Complete thin API layer for Shopee Bridge ERPNext integration. All functions follow the pattern `shopee_bridge.api.function_name()` and return consistent JSON responses.

## 🎯 API Design Principles

- **Thin Layer**: API functions are lightweight wrappers that delegate to service modules
- **Consistent Response**: All functions return `{"ok": true, "data": ...}` or `{"ok": false, "error": "..."}`
- **Error Handling**: Exceptions are caught and logged, never exposed to clients
- **Input Validation**: Basic parameter validation and type coercion
- **Service Delegation**: Business logic lives in `/services/` and `/jobs/` modules

## 🔐 Authentication & Connection

### `shopee_bridge.api.connect_to_shopee(scopes=None)`
Generate OAuth authorization URL for Shopee connection.

**Parameters:**
- `scopes` (optional): List of OAuth scopes. Defaults to `["shop.basic.info", "order", "payment", "returns", "logistics"]`

**Usage:**
```python
frappe.call("shopee_bridge.api.connect_to_shopee", {
    "scopes": ["order", "payment"]
})
```

**Response:**
```json
{
    "ok": true,
    "url": "https://partner.shopeemobile.com/api/v2/shop/auth_partner?..."
}
```

### `shopee_bridge.api.oauth_callback(**params)`
Handle OAuth redirect callback from Shopee.

**Parameters:**
- `**params`: All callback parameters from Shopee redirect

**Usage:**
```python
frappe.call("shopee_bridge.api.oauth_callback", {
    "code": "auth_code",
    "shop_id": "12345"
})
```

### `shopee_bridge.api.test_shopee_connection()`
Test current Shopee API connection and return shop info.

**Response:**
```json
{
    "ok": true,
    "shop": {
        "shop_id": 12345,
        "shop_name": "My Store",
        "region": "ID"
    }
}
```

### `shopee_bridge.api.refresh_token()`
Manually refresh OAuth access token.

**Response:**
```json
{
    "ok": true,
    "token_refreshed": true
}
```

## 📦 Orders API

### `shopee_bridge.api.get_order(order_sn)`
Get detailed information for a specific order from Shopee.

**Parameters:**
- `order_sn` (required): Shopee order serial number

**Usage:**
```python
frappe.call("shopee_bridge.api.get_order", {
    "order_sn": "2012345678901234"
})
```

### `shopee_bridge.api.sync_order(order_sn)`
Sync specific order from Shopee to ERPNext.

**Parameters:**
- `order_sn` (required): Shopee order serial number

### `shopee_bridge.api.sync_orders_api(minutes=15)`
Trigger incremental order sync job.

**Parameters:**
- `minutes` (optional): Lookback window in minutes. Default: 15

### `shopee_bridge.api.update_order_status(order_sn, status)`
Update order status in Shopee.

**Parameters:**
- `order_sn` (required): Shopee order serial number
- `status` (required): New status value

## 🚚 Logistics API

### `shopee_bridge.api.get_shipping_info(order_sn)`
Get shipping information for an order.

**Parameters:**
- `order_sn` (required): Shopee order serial number

### `shopee_bridge.api.sync_shipping_api(minutes=30)`
Trigger shipping information sync job.

**Parameters:**
- `minutes` (optional): Lookback window in minutes. Default: 30

### `shopee_bridge.api.update_tracking(order_sn, tracking_number)`
Update tracking number for an order.

**Parameters:**
- `order_sn` (required): Shopee order serial number
- `tracking_number` (required): Tracking number from logistics provider

## 🔄 Returns API

### `shopee_bridge.api.get_returns(order_sn=None)`
Get returns data, optionally filtered by order.

**Parameters:**
- `order_sn` (optional): Filter returns by specific order

### `shopee_bridge.api.sync_returns_api(minutes=60)`
Trigger returns sync job.

**Parameters:**
- `minutes` (optional): Lookback window in minutes. Default: 60

### `shopee_bridge.api.process_return(return_sn, action)`
Process return request (approve/reject/refund).

**Parameters:**
- `return_sn` (required): Shopee return serial number
- `action` (required): Action to take ("approve", "reject", "refund")

## 💰 Finance API

### `shopee_bridge.api.get_payout_info(batch_id=None)`
Get payout/escrow information.

**Parameters:**
- `batch_id` (optional): Specific payout batch ID

### `shopee_bridge.api.sync_finance_api()`
Trigger finance sync job (escrow batches).

### `shopee_bridge.api.reconcile_bank_api()`
Trigger bank reconciliation job.

### `shopee_bridge.api.sync_escrow_batch(batch_id)`
Sync specific escrow batch.

**Parameters:**
- `batch_id` (required): Payout batch ID to sync

## 🔗 Webhook API

### `shopee_bridge.api.webhook_live()` [POST, Guest Access]
Live webhook endpoint for Shopee notifications.

### `shopee_bridge.api.webhook_test()` [POST, Guest Access]  
Test webhook endpoint for Shopee notifications.

### `shopee_bridge.api.get_webhook_logs(limit=50)`
Get recent webhook processing logs.

**Parameters:**
- `limit` (optional): Number of logs to retrieve (1-500). Default: 50

**Response:**
```json
{
    "ok": true,
    "logs": [
        {
            "name": "WHK-001",
            "event_type": "order_update",
            "source_env": "live",
            "status": "done",
            "signature_valid": 1,
            "creation": "2023-09-05 10:30:00"
        }
    ]
}
```

### `shopee_bridge.api.retry_webhook(inbox_name)`
Manually retry a failed webhook.

**Parameters:**
- `inbox_name` (required): Webhook inbox document name

## 🔧 Utility API

### `shopee_bridge.api.get_health_status()`
Get overall system health status.

**Response:**
```json
{
    "ok": true,
    "health": {
        "token_valid": true,
        "recent_errors": 0,
        "pending_webhooks": 2,
        "settings_configured": true,
        "timestamp": "2023-09-05 10:30:00"
    }
}
```

## 🚀 Usage Examples

### JavaScript (Frontend)
```javascript
// Get order details
frappe.call({
    method: "shopee_bridge.api.get_order",
    args: {
        order_sn: "2012345678901234"
    },
    callback: function(r) {
        if (r.message.ok) {
            console.log("Order:", r.message.order);
        } else {
            frappe.msgprint("Error: " + r.message.error);
        }
    }
});

// Trigger order sync
frappe.call({
    method: "shopee_bridge.api.sync_orders_api",
    args: {
        minutes: 30
    },
    callback: function(r) {
        if (r.message.ok) {
            frappe.msgprint("Sync completed");
        }
    }
});
```

### Python (Server-side)
```python
import frappe

# Get order details
result = frappe.get_attr("shopee_bridge.api.get_order")("2012345678901234")
if result["ok"]:
    order_data = result["order"]
else:
    frappe.log_error(result["error"])

# Health check
health = frappe.get_attr("shopee_bridge.api.get_health_status")()
if health["ok"]:
    print(f"System health: {health['health']}")
```

### REST API (External)
```bash
# Get webhook logs
curl -X GET "https://your-erpnext.com/api/method/shopee_bridge.api.get_webhook_logs?limit=10" \
  -H "Authorization: token api_key:api_secret"

# Trigger order sync
curl -X POST "https://your-erpnext.com/api/method/shopee_bridge.api.sync_orders_api" \
  -H "Authorization: token api_key:api_secret" \
  -H "Content-Type: application/json" \
  -d '{"minutes": 30}'
```

## 🔒 Security Notes

- All API functions use `@frappe.whitelist()` decorator for access control
- Webhook endpoints use `allow_guest=True` but verify signatures internally
- Error messages are sanitized before returning to clients
- All exceptions are logged server-side for debugging
- Input validation prevents common attack vectors

## 📊 Error Handling

All API functions follow consistent error handling:

```json
// Success Response
{
    "ok": true,
    "data": { /* response data */ }
}

// Error Response  
{
    "ok": false,
    "error": "Human readable error message"
}
```

Detailed error information is logged server-side in ERPNext Error Logs for debugging.