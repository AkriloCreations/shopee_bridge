import hashlib
import hmac

from .utils import _base, _settings, sync_orders_range

import requests
import time
import frappe # pyright: ignore[reportMissingImports]


def _sign(key: str, s: str) -> str:
    return hmac.new((key or "").strip().encode(), s.encode(), hashlib.sha256).hexdigest()


def _call(path: str, partner_id: str, partner_key: str,
    shop_id: str | None = None, access_token: str | None = None,
    params: dict | None = None, timeout: int = 30):
    ts = int(time.time())

    payload = f"{partner_id}{path}{ts}{access_token or ''}{shop_id or ''}"
    sign = _sign(partner_key, payload)

    q = {
        "partner_id": partner_id,
        "timestamp": ts,
        "sign": sign,
    }
    if access_token:
        q["access_token"] = access_token
    if shop_id:
        q["shop_id"] = shop_id

    url = f"{_base()}{path}"

    # Heuristic: endpoint yang mengandung 'get_' → gunakan GET + querystring
    use_get = path.startswith("/api/v2/") and ("/get_" in path or path.endswith("/get"))
    try:
        if use_get:
            qp = dict(q)
            if params:
                for k, v in params.items():
                    qp[k] = str(v)
            r = requests.get(url, params=qp, timeout=timeout)
        else:
            r = requests.post(
                url,
                params=q,
                json=(params or {}),
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )

        if r.headers.get("content-type", "").startswith("application/json"):
            data = r.json()
        else:
            data = {"error": "HTTP", "message": r.text}

        if isinstance(data, list):
            data = {"response": {"_list_payload": data}}

        # FIX: Add detailed logging untuk debug
        if path == "/api/v2/shop/get_shop_info":
            frappe.logger().info(f"Shopee API {path} - Status: {r.status_code}")
            frappe.logger().info(f"Shopee API {path} - Response: {data}")
            frappe.logger().info(f"Shopee API {path} - URL: {url}")
            frappe.logger().info(f"Shopee API {path} - Params: {qp if use_get else q}")

        return data
    except requests.exceptions.RequestException as e:
        frappe.log_error(f"Shopee API {path} request failed: {str(e)}", "Shopee API Call")
        return {"error": "REQUEST_ERROR", "message": str(e)}


@frappe.whitelist()
def connect_url(app_type: str = "shop"):
    """Bangun URL OAuth. app_type: 'shop' (Seller API) atau 'merchant' (CB/Merchant)."""
    from urllib.parse import quote
    s = frappe.get_single("Shopee Settings")

    path = "/api/v2/shop/auth_partner" if app_type == "shop" else "/api/v2/merchant/auth_partner"
    ts = int(time.time())

    partner_id = str(s.partner_id).strip()
    partner_key = (s.partner_key or "").strip()

    # sign base string: partner_id + path + timestamp (NO access_token/shop_id for auth)
    sign = _sign(partner_key, f"{partner_id}{path}{ts}")

    # Multiple redirect options - choose the one that works best for your setup
    redirect_options = [
        "https://erp.managerio.ddns.net/app/shopee-settings",  # Direct to settings
        "https://erp.managerio.ddns.net/oauth-callback",       # Via callback page
        "https://erp.managerio.ddns.net/api/method/.doctype.shopee_settings.api.oauth_callback_handler"  # Direct API
    ]

    # Use the first option by default, but make it configurable
    redirect = getattr(s, 'oauth_redirect_url', None) or redirect_options[0]

    # urutan param disusun seperti contoh resmi Shopee: partner_id, redirect, timestamp, sign
    url = (
        f"{_base()}{path}"
        f"?partner_id={partner_id}"
        f"&redirect={quote(redirect,safe='')}"
        f"&timestamp={ts}"
        f"&sign={sign}"
    )

    return {
        "url": url,
        "redirect_url": redirect,
        "partner_id": partner_id,
        "timestamp": ts,
        "signature": sign
    }


@frappe.whitelist()
def exchange_code(code: str, shop_id: str | None = None):
    """
    Manual: tukar code -> access_token & refresh_token, simpan di Shopee Settings.
    Panggil dari Client Script.
    """
    if not code or not code.strip():
        frappe.throw("Authorization code is required")

    s = _settings()

    partner_id = str(s.partner_id).strip()
    partner_key = (s.partner_key or "").strip()

    if not partner_id or not partner_key:
        frappe.throw("Partner ID and Partner Key must be configured in Shopee Settings")

    ts = int(time.time())
    # Ganti endpoint sesuai dokumentasi Shopee
    path = "/api/v2/auth/access_token/get"  # ← Perubahan utama
    base_string = f"{partner_id}{path}{ts}"
    sign = _sign(partner_key, base_string)

    url = f"{_base()}{path}?partner_id={partner_id}&timestamp={ts}&sign={sign}"
    body = {
        "code": code,
        "partner_id": int(partner_id)
    }

    if shop_id:
        body["shop_id"] = int(shop_id)

    try:
        r = requests.post(url, json=body, headers={"Content-Type": "application/json"}, timeout=30)

        if r.headers.get("content-type", "").startswith("application/json"):
            data = r.json()
        else:
            frappe.throw(f"Invalid response from Shopee: {r.text}")

    except requests.exceptions.RequestException as e:
        frappe.throw(f"Request to Shopee failed: {str(e)}")

    # Check for API errors
    if data.get("error"):
        error_msg = data.get("message", "Unknown error")
        frappe.throw(f"Shopee API error: {data.get('error')} - {error_msg}")

    # Extract response data (bisa nested atau langsung)
    response_data = data.get("response", data)

    if not response_data.get("access_token"):
        frappe.throw("No access token received from Shopee")

    # Update settings with new tokens
    s.access_token = response_data.get("access_token")
    s.refresh_token = response_data.get("refresh_token")
    s.token_expire_at = ts + int(response_data.get("expire_in", 0))

    if shop_id:
        s.shop_id = shop_id

    s.save(ignore_permissions=True)
    frappe.db.commit()

    return {
        "ok": True,
        "shop_id": s.shop_id,
        "expire_at": s.token_expire_at,
        "access_token_preview": s.access_token[:10] + "..." if s.access_token else None
    }


@frappe.whitelist(allow_guest=True)
def oauth_callback(code=None, shop_id=None, **kw):
    """Tukar code → access_token & refresh_token lalu simpan."""
    if not code:
        frappe.throw("Authorization code is required")

    s = _settings()

    # Use exchange_code function for consistency
    try:
        result = exchange_code(code, shop_id)
        return "Shopee connected successfully"
    except Exception as e:
        frappe.throw(f"OAuth callback failed: {str(e)}")


@frappe.whitelist()
def refresh_if_needed(force: int = 0):
    import time, requests, hmac, hashlib
    s = _settings()
    if not s.refresh_token:
        return {"status": "no_refresh_token"}

    safety  = int(getattr(s, "overlap_seconds", 600) or 300)
    now_ts  = int(time.time())
    if not force and s.token_expire_at and (int(s.token_expire_at) - now_ts) > safety:
        return {"status": "token_still_valid", "expires_in": int(s.token_expire_at) - now_ts}

    partner_id  = str(s.partner_id).strip()
    partner_key = (s.partner_key or "").strip()
    path = "/api/v2/auth/access_token/get"
    ts   = int(time.time())

    # make signature (hex lowercase)
    base_string = f"{partner_id}{path}{ts}".encode()
    digest = hmac.new(partner_key.encode(), base_string, hashlib.sha256).hexdigest()

    # IMPORTANT: put sign in QUERY via params=
    params = {
        "partner_id": partner_id,
        "timestamp": ts,
        "sign": digest,
    }

    body = {
        "partner_id": int(partner_id),
        "refresh_token": s.refresh_token,
    }
    if s.shop_id:
        body["shop_id"] = int(s.shop_id)
    elif getattr(s, "merchant_id", None):
        body["merchant_id"] = int(s.merchant_id)

    try:
        r = requests.post(
            f"{_base()}{path}",
            params=params,                     # <- querystring here
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        if "application/json" not in (r.headers.get("content-type") or ""):
            frappe.log_error(f"Bad CT: {r.headers.get('content-type')} | {r.text}", "Shopee Token Refresh")
            return {"status": "error", "message": "Invalid response content-type"}
        data = r.json()
    except requests.exceptions.RequestException as e:
        frappe.log_error(f"Refresh request failed: {e}", "Shopee Token Refresh")
        return {"status": "error", "message": str(e)}

    if data.get("error"):
        return {"status": "error", "message": data.get("message") or "Unknown", "request_id": data.get("request_id")}

    new_access  = data.get("access_token")
    new_refresh = data.get("refresh_token") or s.refresh_token
    expire_in   = int(data.get("expire_in") or 0)
    if not new_access or not expire_in:
        frappe.log_error(f"No token/expiry in resp: {data}", "Shopee Token Refresh")
        return {"status": "no_new_token"}

    s.access_token    = new_access
    s.refresh_token   = new_refresh
    s.token_expire_at = ts + expire_in
    s.last_success_update_time = now_ts
    s.save(ignore_permissions=True)
    frappe.db.commit()
    return {"status": "refreshed", "expires_in": expire_in, "request_id": data.get("request_id")}


@frappe.whitelist()
def debug_sign():
    """Debug signature generation"""
    s = frappe.get_single("Shopee Settings")
    path = "/api/v2/shop/auth_partner"  # Seller/Shop API
    ts = int(time.time())

    partner_id = str(s.partner_id).strip()
    partner_key = (s.partner_key or "").strip()
    base = f"{partner_id}{path}{ts}"
    sign = _sign(partner_key, base)

    return {
        "partner_id": partner_id,
        "partner_key_length": len(partner_key),
        "partner_key_first_10": partner_key[:10] + "..." if len(partner_key) > 10 else partner_key,
        "path": path,
        "timestamp": ts,
        "base_string": base,
        "signature": sign,
        "url": f"{_base()}{path}?partner_id={partner_id}&timestamp={ts}&sign={sign}",
        "environment": s.environment
    }


# ====== SYNC ITEMS (PASTE/REPLACE FUNGSI LAMA) ==============================

@frappe.whitelist()
def test_connection():
    """Test Shopee API connection and token validity."""
    try:
        s = _settings()

        if not s.access_token:
            return {"success": False, "error": "No access token configured"}

        # Test with shop info API
        result = _call("/api/v2/shop/get_shop_info",
                      str(s.partner_id).strip(), s.partner_key,
                      s.shop_id, s.access_token, {})

        # FIX: Add manual logging untuk debug
        print(f"DEBUG: Shopee API response: {result}")
        frappe.log_error(f"DEBUG: Shopee API response: {result}", "Shopee Debug")

        # FIX: Add better error handling and logging
        if result.get("error"):
            # FIX: Gunakan title yang pendek untuk log
            frappe.log_error(f"Connection test failed: {result.get('error')} - {result.get('message')}", "Shopee Test")

            # Try to refresh token if expired
            if "access token expired" in str(result.get("message", "")).lower():
                refresh_result = refresh_if_needed()
                if refresh_result.get("status") == "refreshed":
                    # Retry with new token
                    result = _call("/api/v2/shop/get_shop_info",
                                  str(s.partner_id).strip(), s.partner_key,
                                  s.shop_id, s.access_token, {})

                    if result.get("error"):
                        return {"success": False, "error": result.get("error"), "message": result.get("message")}

            if result.get("error"):
                return {"success": False, "error": result.get("error"), "message": result.get("message")}

        shop_info = result.get("response", {})

        # FIX: Check if we actually got shop data
        if not shop_info or not shop_info.get("shop_name"):
            # FIX: Log dengan title pendek
            frappe.log_error(f"Empty shop info returned: {result}", "Shopee Test")
            return {"success": False, "error": "No shop information returned", "message": "API call succeeded but returned empty data"}

        return {
            "success": True,
            "shop_name": shop_info.get("shop_name"),
            "shop_id": shop_info.get("shop_id"),
            "region": shop_info.get("region"),
            "status": shop_info.get("status")
        }

    except Exception as e:
        # FIX: Log exception dengan title pendek
        frappe.log_error(f"Connection test exception: {str(e)}", "Shopee Test")
        return {"success": False, "error": "exception", "message": str(e)}


## _norm_esc removed; using _normalize_escrow_payload from webhook

@frappe.whitelist()
def sync_recent_orders(hours: int = 24, page_size: int = 50):
    """Realtime wrapper yang memanfaatkan logic inti `sync_orders_range`.
    Hitung window (last_success_update_time ± overlap) lalu panggil `sync_orders_range` agar single source of truth.
    Return shape dipertahankan (range_mode=False)."""
    import time as _t
    now = int(_t.time())
    s = _settings()
    last = int(getattr(s, "last_success_update_time", 0) or 0)
    overlap = int(getattr(s, "overlap_seconds", 600) or 600)
    if last == 0:
        time_from = now - int(hours) * 3600
    else:
        time_from = max(0, last - overlap)
    time_to = now
    # Accept order_status passthrough for UI flexibility
    order_status = getattr(s, "order_status", None)
    try:
        res = sync_orders_range(time_from=time_from, time_to=time_to, page_size=page_size, order_status=order_status)
    except Exception as e:
        frappe.log_error(f"sync_recent_orders wrapper failed: {e}", "Shopee Sync Wrapper")
        raise
    res["range_mode"] = False
    res["recent_mode"] = True
    res["from"] = time_from
    res["to"] = time_to
    res["source"] = "sync_recent_orders_wrapper"
    return res


# Scheduled job functions (called by ERPNext scheduler)
def scheduled_order_sync():
    """Scheduled function to sync recent orders (called by scheduler)."""
    try:
        frappe.logger().info("Starting scheduled order sync")
        result = sync_recent_orders(hours=24)  # Sync last 24 hours

        if result.get("errors", 0) > 0:
            frappe.logger().warning(f"Order sync completed with {result.get('errors')} errors")
        else:
            frappe.logger().info(f"Order sync completed successfully: {result.get('processed_orders')} orders processed")

    except Exception as e:
        frappe.log_error(f"Scheduled order sync failed: {str(e)}", "Scheduled Order Sync")


def scheduled_token_refresh():
    """Scheduled function to refresh token if needed (called by scheduler)."""
    try:
        result = refresh_if_needed()
        if result.get("status") == "refreshed":
            frappe.logger().info("Token refreshed successfully")
    except Exception as e:
        frappe.log_error(f"Scheduled token refresh failed: {str(e)}", "Scheduled Token Refresh")


def scheduled_hourly_sync():
    """Scheduled function to sync orders hourly (backup)."""
    try:
        frappe.logger().info("Starting hourly order sync")
        result = sync_recent_orders(hours=1)  # Sync last hour

        if result.get("errors", 0) > 0:
            frappe.logger().warning(f"Hourly sync completed with {result.get('errors')} errors")
        else:
            frappe.logger().info(f"Hourly sync completed successfully: {result.get('processed_orders')} orders processed")

    except Exception as e:
        frappe.log_error(f"Hourly order sync failed: {str(e)}", "Hourly Order Sync")


def scheduled_cleanup():
    """Scheduled function to cleanup old data."""
    try:
        frappe.logger().info("Starting scheduled cleanup")
        # Add cleanup logic here if needed
        frappe.logger().info("Cleanup completed successfully")
    except Exception as e:
        frappe.log_error(f"Scheduled cleanup failed: {str(e)}", "Scheduled Cleanup")


@frappe.whitelist()
def manual_token_refresh():
    """Manual refresh token - bisa dipanggil dari client script"""
    try:
        result = refresh_if_needed()
        return {
            "status": "success" if result.get("status") == "refreshed" else result.get("status", "unknown"),
            "message": result.get("message", "Token refresh completed"),
            "data": result
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Manual Token Refresh Error")
        return {
            "status": "error",
            "message": str(e)
        }


@frappe.whitelist()
def get_oauth_url():
    """Generate OAuth URL untuk mendapatkan authorization code"""
    try:
        result = connect_url("shop")
        return {
            "status": "success",
            "message": "OAuth URL generated successfully",
            "data": {
                "oauth_url": result.get("url"),
                "redirect_url": result.get("redirect_url"),
                "partner_id": result.get("partner_id")
            }
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "OAuth URL Generation Error")
        return {
            "status": "error",
            "message": str(e)
        }


@frappe.whitelist()
def get_token_status():
    """Get current token status and info"""
    try:
        s = _settings()

        token_valid = bool(s.access_token)
        expires_soon = False
        expires_in = None

        if s.token_expire_at:
            import time
            current_time = int(time.time())
            expires_in = int(s.token_expire_at) - current_time
            expires_soon = expires_in < 300  # Less than 5 minutes

        return {
            "status": "success",
            "data": {
                "has_access_token": token_valid,
                "has_refresh_token": bool(s.refresh_token),
                "shop_id": s.shop_id,
                "partner_id": s.partner_id,
                "environment": s.environment,
                "token_expires_in": expires_in,
                "token_expires_soon": expires_soon,
                "access_token_preview": s.access_token[:20] + "..." if s.access_token else None
            }
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }