# Shopee OAuth Authorization and Token Refresh Flow

This document describes how to acquire authorizations from shop accounts and main accounts, and how to obtain and refresh access tokens using the Shopee API.

---

## 1. Authorization Process

### 1.1 Shop Account Authorization

1. Share the authorization link with the seller. They will log in, enter the verification code sent to their phone, and click **Confirm Authorization**.
2. After authorization, Shopee redirects to:

```
https://your.redirect.url/?code=<authorization_code>&shop_id=<shop_id>
```

- `code`: single-use OAuth code (expires in 10 minutes)
- `shop_id`: the ID of the authorized shop

### 1.2 Main Account Authorization

1. Seller switches to Main Account, logs in, and selects multiple shops.
2. If using Cross-Border API, remind seller to check **Auth Merchant**.
3. After authorization, Shopee redirects to:

```
https://your.redirect.url/?code=<authorization_code>&main_account_id=<main_account_id>
```

- `main_account_id`: ID of the main account

---

## 2. Obtaining Access Token (GetAccessToken)

**Endpoint**:

```
POST https://partner.shopeemobile.com/api/v2/auth/token/get
```

**Query Parameters**:
- `partner_id` (int): your App’s partner_id
- `timestamp` (int): current UNIX timestamp (valid ±5 minutes)
- `sign` (string): HMAC-SHA256 of `partner_id + path + timestamp`

**Body**:
```json
{
  "code": "<authorization_code>",
  "partner_id": <partner_id>,
  // use either shop_id or main_account_id
  "shop_id": <shop_id>,
  "main_account_id": <main_account_id>
}
```

**Response**:
```json
{
  "access_token": "...",     // valid for 4 hours
  "refresh_token": "...",    // valid for 30 days
  "expires_in": 14400,
  "shop_id": <shop_id>,       // when using shop flow
  "merchant_id": <merchant_id>, // when using main account flow
  // other fields...
}
```

---

## 3. Refreshing Access Token (RefreshAccessToken)

**Endpoint**:

```
POST https://partner.shopeemobile.com/api/v2/auth/access_token/get
```

**Query Parameters**:
- `partner_id`, `timestamp`, `sign` (same as above)

**Body**:
```json
{
  "partner_id": <partner_id>,
  "refresh_token": "<refresh_token>",
  // either shop_id or merchant_id
  "shop_id": <shop_id>,
  "merchant_id": <merchant_id>
}
```

**Response**:
```json
{
  "access_token": "...",    // new token, valid 4 hours
  "refresh_token": "...",   // new refresh token, valid 30 days
  "expires_in": 14400,
  "shop_id": <shop_id>,      // for shop refresh
  "merchant_id": <merchant_id> // for merchant refresh
}
```

**Notes**:
- Call this within the 4-hour validity to obtain new tokens.
- Each new refresh produces independent tokens per shop/merchant.
- Old access tokens remain valid for up to 5 minutes after refresh.

---

## 4. Signature Generation

For both endpoints, generate `sign` as:

```python
import hmac, hashlib
base_string = f"{partner_id}{path}{timestamp}"
sign = hmac.new(partner_key.encode(), base_string.encode(), hashlib.sha256).hexdigest()
```

Replace `<path>` with `/api/v2/auth/token/get` or `/api/v2/auth/access_token/get` accordingly.

---

Implement these flows in your Shopee Bridge integration to handle OAuth exchanges and token refresh cycles reliably.
