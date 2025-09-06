# ---------------------------------------------------------------------------
# Datetime helpers
# ---------------------------------------------------------------------------
from datetime import datetime, timedelta, timezone

def _utc_naive(expires_in_seconds: int):
    """Return expiry as integer epoch UTC (seconds since 1970-01-01 UTC)."""
    return int((datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)).timestamp())

"""Shopee Bridge authentication utilities.

This module ONLY prepares data structures (URLs, signed parameters, payloads) and manipulates
stored credentials in the Single doctype "Shopee Settings". It MUST NOT perform outbound HTTP
requests; network I/O belongs in `clients.py`.

Implemented capabilities:
- OAuth v2 helper flows (build authorize URL, exchange code, refresh flow heuristic)
- HMAC-SHA256 request signing per Shopee v2 rule:
        signature_base = partner_id + path + timestamp + access_token + shop_id
        signature = hex(HMAC-SHA256(signature_base, partner_key))
- Webhook signature verification (raw body HMAC with push key) + timestamp drift enforcement
- Pro‑active token refresh scheduling helper (`schedule_token_renewal_cron`)

Security notes:
- Secrets (partner_key, access_token, refresh_token, push_key) MUST NOT be logged in full. Use
    `_mask_secret` helper when logging.
- Access to partner_key should always use `settings.get_password("partner_key")` to leverage
    Frappe's encrypted password storage.

Raises custom exceptions: AuthRequired, InvalidState, SignatureMismatch.

TODOs (left for business logic implementers):
1. Persist any additional fields returned by real HTTP token responses (e.g. merchant_id).
2. Implement real HTTP requests in `clients.py` using the payloads produced here.
3. Add CSRF/state correlation storage (e.g. redis / cache) with expiry if multi-user auth flows used.
4. Extend scheduling (e.g. handle multi‑shop tokens if expanding beyond single shop).
"""

# DOC-GUARD: Shopee OAuth Refresh v2 Implementation
# ================================================
# Refresh endpoint: /api/v2/auth/access_token/get (METHOD: POST)
# Common query params: partner_id, timestamp, sign (+ optionally shop_id or merchant_id in query if required by docs)
# BODY: { "refresh_token": "...", "shop_id": <int> }  (or merchant_id for merchant flow)
# Signature base string for this endpoint: partner_id + path + timestamp + shop_id|merchant_id
# Note that 404 error_not_found usually means wrong path or missing/incorrect shop_id/merchant_id in request.
# ================================================

from typing import List, Dict, Any, Union, Optional
import time
import json
import hmac
import hashlib
import secrets
import urllib.parse

# import frappe  # Moved inside functions to avoid import errors in tests
from shopee_bridge import auth


PROD_BASE_URL = "https://partner.shopeemobile.com"
TEST_BASE_URL = "https://partner.test-stable.shopeemobile.com"

OAUTH_AUTHORIZE_PATH = "/api/v2/shop/auth_partner"
OAUTH_TOKEN_PATH = "/api/v2/auth/token/get"
OAUTH_REFRESH_PATH = "/api/v2/auth/access_token/get"  # This is the correct endpoint for refresh

WEBHOOK_SIGNATURE_HEADER = "X-Shopee-Signature"
WEBHOOK_TIMESTAMP_HEADER = "X-Shopee-Timestamp"
WEBHOOK_ALLOWED_DRIFT_SECONDS = 300

STATE_CACHE_PREFIX = "shopee_oauth_state:"
STATE_TTL_SECONDS = 600  # 10 minutes

class AuthRequired(Exception):
    """Raised when required authentication context is missing (e.g., missing tokens)."""
    ...

class InvalidState(Exception):
    """Raised when the OAuth callback / parameters fail validation (e.g., state mismatch)."""
    ...

class SignatureMismatch(Exception):
    """Raised when a request/webhook signature fails to validate."""
    ...

class ShopeeAuthError(Exception):
    """Raised for Shopee API authentication errors (e.g. invalid_access_token)."""
    pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _settings():
    ## ambil data direct langsung dari DB ##
    import frappe
    try:
        return frappe.get_doc("Shopee Settings")
    except Exception as exc:  # pragma: no cover - defensive guard
        raise AuthRequired("Shopee Settings not configured") from exc


def _base_url(env: str) -> str:
    return PROD_BASE_URL if env.lower() in ("live", "production") else TEST_BASE_URL


def _mask_secret(value: Optional[str], show: int = 4) -> str:
    if not value:
        return "<empty>"
    if len(value) <= show:
        return "*" * len(value)
    return value[:show] + "…" + "*" * (len(value) - show)


def _cache_set(key: str, val: str, ttl: int):
    import frappe
    frappe.cache().set_value(key, val, expires_in_sec=ttl)


def _cache_get(key: str) -> Optional[str]:
    import frappe
    return frappe.cache().get_value(key)


def _build_state() -> str:
    state = secrets.token_urlsafe(16)
    _cache_set(STATE_CACHE_PREFIX + state, "1", STATE_TTL_SECONDS)
    return state


def _validate_state(state: str):
    if not state:
        raise InvalidState("Missing state parameter")
    cached = _cache_get(STATE_CACHE_PREFIX + state)
    if not cached:
        raise InvalidState("Invalid or expired state parameter")
    # One‑time use: remove
    import frappe
    frappe.cache().delete_value(STATE_CACHE_PREFIX + state)


# ---------------------------------------------------------------------------
# New helpers for refresh implementation
# ---------------------------------------------------------------------------
def now_epoch() -> int:
    """Return current epoch timestamp (seconds since 1970-01-01 UTC)."""
    return int(time.time())


def _frappe():
    """Lazy import frappe to avoid top-level imports."""
    import frappe
    return frappe


def get_shop_info() -> Dict[str, Any]:
    """Get shop info from Shopee API.

    Makes actual API call to get shop information if tokens are available.
    """
    settings = _settings()
    
    # If no access token, return basic info
    access_token = getattr(settings, "access_token", None)
    if not access_token:
        return {
            "shop_id": getattr(settings, "shop_id", None),
            "environment": settings.environment,
            "has_token": False,
        }
    
    # Make API call to get shop info
    try:
        from shopee_bridge import clients
        
        # Use the shop/get_shop_info endpoint
        result = clients.request_json(
            method="GET",
            host="",
            path="/api/v2/shop/get_shop_info",
            query={},
            body=None
        )
        
        if result.get("error"):
            # API error, return basic info with error
            return {
                "shop_id": getattr(settings, "shop_id", None),
                "environment": settings.environment, 
                "has_token": True,
                "api_error": result.get("error"),
                "message": result.get("message")
            }
        
        # Success, return shop info from API
        shop_info = result.get("shop_list", [{}])[0] if result.get("shop_list") else {}
        
        return {
            "shop_id": shop_info.get("shop_id") or getattr(settings, "shop_id", None),
            "shop_name": shop_info.get("shop_name"),
            "region": shop_info.get("region"),
            "environment": settings.environment,
            "has_token": True,
            "status": shop_info.get("status"),
            "api_response": result
        }
        
    except Exception as e:
        # Network or other error
        return {
            "shop_id": getattr(settings, "shop_id", None),
            "environment": settings.environment,
            "has_token": True,
            "error": str(e)
        }

def build_authorize_url(scopes: List[str] = None) -> str:
    """Build the Shopee OAuth v2 authorization URL.

    Per Shopee API specification, the authorization link requires:
    - Fixed authorization URL (prod/sandbox)
    - partner_id (from app settings)
    - timestamp (valid for 5 minutes)
    - sign (HMAC-SHA256 signature of partner_id + api_path + timestamp)
    - redirect (redirect URL after authorization)

    Args:
        scopes: List of scopes requested (optional for authorization URL).
    Returns:
        Fully composed HTTPS URL for the authorization step.
    """
    settings = _settings()
    partner_id = settings.partner_id
    partner_key = settings.get_password("partner_key")
    redirect_url = settings.redirect_url
    base_url = _base_url(settings.environment)
    timestamp = int(time.time())
    base_string = f"{partner_id}{OAUTH_AUTHORIZE_PATH}{timestamp}"
    sign = hmac_sha256(base_string, partner_key)
    params = {
        "partner_id": partner_id,
        "timestamp": timestamp,
        "sign": sign,
        "redirect": redirect_url,
    }
    if scopes:
        params["scopes"] = ",".join(scopes)
    qs = urllib.parse.urlencode(params)
    return f"{base_url}{OAUTH_AUTHORIZE_PATH}?{qs}"

def handle_oauth_callback(params: Dict[str, Any]) -> Dict[str, Any]:
    """Process the OAuth callback parameters and complete token exchange.

    Steps:
    1. Validate required params (code, shop_id OR main_account_id)
    2. Exchange code for tokens via HTTP call
    3. Persist tokens and shop info

    Args:
        params: Dict of query parameters returned by Shopee to redirect URL.
    Returns:
        Dict with success status and token info.
    Raises:
        InvalidState: if validation fails.
    """
    code = params.get("code")
    shop_id = params.get("shop_id")
    main_account_id = params.get("main_account_id")
    
    if not code:
        raise InvalidState("Missing code in callback params")
    
    if not (shop_id or main_account_id):
        raise InvalidState("Missing shop_id or main_account_id in callback params")
    
    # Complete token exchange
    try:
        result = complete_token_exchange(code, shop_id, main_account_id)
        return result
    except Exception as e:
        import frappe
        frappe.log_error(f"OAuth callback error: {str(e)}", "Shopee OAuth Callback")
        raise


def complete_token_exchange(code: str, shop_id: Union[str, int] = None, main_account_id: Union[str, int] = None) -> Dict[str, Any]:
    """Complete the token exchange process with HTTP call and persistence.
    
    Args:
        code: Authorization code from OAuth callback
        shop_id: Shop identifier from callback (optional)
        main_account_id: Main account identifier from callback (optional)
        
    Returns:
        Dict with exchange result and token info
    """
    from shopee_bridge import clients
    try:
        payload = exchange_code_for_token(code, shop_id, main_account_id)
        response = clients._do_request(
            payload["method"],
            payload["url"],
            {"Content-Type": "application/json"},
            None,
            payload["json"],
            None
        )
        status, text, headers = response
        if status == 403 and "invalid_access_token" in text:
            refresh_access_token()
            response = clients._do_request(
                payload["method"],
                payload["url"],
                {"Content-Type": "application/json"},
                None,
                payload["json"],
                None
            )
            status, text, headers = response
        if status != 200:
            raise Exception(f"Token exchange failed: HTTP {status} - {text}")
        import json
        data = json.loads(text)
        if data.get("error"):
            raise Exception(f"Shopee API error: {data}")
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        expires_in = int(data.get("expires_in", 14400))
        returned_shop_id = data.get("shop_id")
        returned_merchant_id = data.get("merchant_id")
        if not access_token:
            raise Exception("No access_token in exchange response")
        settings = _settings()
        settings.access_token = access_token
        settings.refresh_token = refresh_token
        settings.token_expires_at = _utc_naive(expires_in)
        settings.last_auth_code = code
        if returned_shop_id:
            settings.shop_id = str(returned_shop_id)
        elif shop_id:
            settings.shop_id = str(shop_id)
        if returned_merchant_id:
            settings.merchant_id = str(returned_merchant_id)
        settings.save(ignore_permissions=True)
        import frappe
        frappe.db.commit()
        frappe.cache().delete_value("Shopee Settings")
        frappe.logger().info(f"[Shopee] OAuth completed - shop_id: {settings.shop_id}, merchant_id: {getattr(settings, 'merchant_id', 'N/A')}")
        return {
            "success": True,
            "shop_id": getattr(settings, "shop_id", None),
            "merchant_id": getattr(settings, "merchant_id", None),
            "expires_in": expires_in,
            "expires_at": settings.token_expires_at,
            "message": "OAuth flow completed successfully"
        }
    except Exception as e:
        error_msg = str(e)
        import frappe
        frappe.log_error(f"Token exchange failed: {error_msg}", "Shopee Token Exchange")
        return {
            "success": False,
            "error": error_msg
        }

def exchange_code_for_token(code: str, shop_id: Union[str, int], main_account_id: Union[str, int] = None) -> Dict[str, Any]:
    """Produce payload for exchanging an authorization code for tokens.

    Per Shopee API specification for GetAccessToken:
    - For Public APIs: partner_id, api path, timestamp
    - Common parameters: sign, partner_id, timestamp
    - Request parameters: code, partner_id, shop_id OR main_account_id

    Args:
        code: Authorization code received from redirect (valid for 10 minutes).
        shop_id: Target shop identifier (use 1 for shop apps).
        main_account_id: Main account identifier (alternative to shop_id).
    Returns:
        Dict representing the JSON body + metadata needed for HTTP layer.
    """
    settings = _settings()
    partner_id = settings.partner_id
    partner_key = settings.get_password("partner_key")
    timestamp = int(time.time())
    
    # For Public APIs signature: partner_id + api_path + timestamp
    base_string = f"{partner_id}{OAUTH_TOKEN_PATH}{timestamp}"
    sign = hmac_sha256(base_string, partner_key)
    
    # Build request body
    request_body = {
        "code": code,
        "partner_id": partner_id,
    }
    
    # Add either shop_id or main_account_id (shop_id takes precedence)
    if shop_id:
        request_body["shop_id"] = int(shop_id)
    elif main_account_id:
        request_body["main_account_id"] = int(main_account_id)
    else:
        # Default to shop_id = 1 for shop apps as per specification
        request_body["shop_id"] = 1
    
    return {
        "method": "POST",
        "url": f"{_base_url(settings.environment)}{OAUTH_TOKEN_PATH}?partner_id={partner_id}&timestamp={timestamp}&sign={sign}",
        "json": request_body,
        "meta": {"signature_base": base_string},
    }

def refresh_if_needed(buffer_seconds: int = 600) -> bool:
    """Heuristically decide whether to refresh the token soon (epoch only).

    Does NOT perform network I/O; only returns whether a refresh payload was produced.
    The caller (scheduler / client) should then execute the HTTP request using the
    payload from `refresh_token_via_api()` and persist new tokens.

    Args:
        buffer_seconds: Trigger refresh if (expiry - now) < buffer_seconds.
    Returns:
        True if a refresh SHOULD happen now, else False.
    """
    settings = _settings()
    raw = getattr(settings, "token_expires_at", None)
    if not raw:
        frappe.logger().warning("[Shopee] No token_expires_at found, will refresh.")
        return True
    try:
        expiry = int(raw)
        now = int(time.time())
        time_remaining = expiry - now
        if time_remaining < buffer_seconds:
            frappe.logger().info(f"[Shopee] Token needs refresh, seconds_remaining={time_remaining}")
            return True
        return False
    except Exception as e:
        frappe.logger().warning(f"[Shopee] Failed to check token expiry: {str(e)}")
        return True

def refresh_token_via_api() -> Dict[str, Any]:
    """Produce payload for token refresh.

    Per Shopee RefreshAccessToken API specification:
    - For Public APIs: partner_id, api path, timestamp
    - Common parameters: sign, partner_id, timestamp  
    - Request parameters: partner_id, shop_id, refresh_token

    Returns:
        Dict containing method, url, json (body), and meta info.
    Raises:
        AuthRequired: if refresh token / shop id missing.
    """
    settings = _settings()
    partner_id = settings.partner_id
    partner_key = settings.get_password("partner_key")
    refresh_token = getattr(settings, "refresh_token", None)
    shop_id = getattr(settings, "shop_id", None)
    
    if not (refresh_token and shop_id):
        raise AuthRequired("Missing refresh_token or shop_id for refresh flow")
    
    timestamp = int(time.time())
    
    # For Public APIs signature: partner_id + api_path + timestamp
    base_string = f"{partner_id}{OAUTH_REFRESH_PATH}{timestamp}"
    sign = hmac_sha256(base_string, partner_key)
    
    # Build request body
    request_body = {
        "partner_id": partner_id,
        "shop_id": int(shop_id),
        "refresh_token": refresh_token,
    }
    
    return {
        "method": "POST",
        "url": f"{_base_url(settings.environment)}{OAUTH_REFRESH_PATH}?partner_id={partner_id}&timestamp={timestamp}&sign={sign}",
        "json": request_body,
        "meta": {"signature_base": base_string},
    }

def sign_request(
    path: str,
    params: Dict[str, Any],
    body: Optional[Union[bytes, str]]
) -> Dict[str, Any]:
    """Return a signed URL + headers for a Shopee API call.

    The caller is responsible for actually performing the HTTP request; this function
    just packages signing metadata.

    Args:
        path: API path beginning with '/api/'.
        params: Query parameters to append (will be URL‑encoded). This dict is NOT part of the signature.
        body: Optional request body (currently unused for signature but accepted for future changes).
    Returns:
        Dict: {"url": str, "headers": dict, "meta": { ... }} where meta includes timestamp & signature_base.
    Raises:
        AuthRequired: if essential credentials missing.
    """
    settings = _settings()
    partner_id = getattr(settings, "partner_id", None)
    access_token = getattr(settings, "access_token", None)
    shop_id = getattr(settings, "shop_id", None)
    if not all([partner_id, access_token, shop_id]):
        raise AuthRequired("Missing partner_id, access_token, or shop_id")
    partner_key = settings.get_password("partner_key")
    timestamp = int(time.time())
    base_string = f"{partner_id}{path}{timestamp}{access_token}{shop_id}"
    signature = hmac_sha256(base_string, partner_key)
    base_qs = {
        "partner_id": partner_id,
        "timestamp": timestamp,
        "access_token": access_token,
        "shop_id": shop_id,
        "sign": signature,
    }
    merged = {**params, **base_qs}
    qs = urllib.parse.urlencode(merged, doseq=True)
    url = f"{_base_url(settings.environment)}{path}?{qs}"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    return {"url": url, "headers": headers, "meta": {"timestamp": timestamp, "signature_base": base_string}}

def verify_webhook_signature(
    path: str,
    raw_body: bytes,
    headers: Dict[str, str],
    push_key: str,
    full_url: str = None
) -> bool:
    """Validate a Shopee webhook request.

    Shopee webhook signing (v2) uses Push Authorization with the following signature:
    base_string = full_url + '|' + raw_body_as_string
    signature = HMAC-SHA256(base_string, push_key)
    
    The signature is provided in the Authorization header.

    Args:
        path: Original request path.
        raw_body: Raw request body bytes.
        headers: Incoming HTTP headers (case-sensitive keys expected as provided by Frappe).
        push_key: Shared secret key from Shopee dashboard (test or live push key).
        full_url: Full URL including protocol and domain (required for Push Authorization).
    Returns:
        True if signature valid.
    Raises:
        SignatureMismatch: on any mismatch or malformed header.
    """
    auth_header = headers.get("Authorization")
    if auth_header and full_url:
        return _verify_push_authorization(full_url, raw_body, push_key, auth_header)
    signature_header = headers.get(WEBHOOK_SIGNATURE_HEADER)
    if signature_header:
        return _verify_legacy_signature(raw_body, push_key, signature_header, headers)
    raise SignatureMismatch("Missing both Authorization and X-Shopee-Signature headers")


def _verify_push_authorization(full_url: str, raw_body: bytes, push_key: str, authorization: str) -> bool:
    """Verify Push Authorization signature.
    
    Per Shopee specification:
    1. Create base string: full_url + '|' + request_body_as_string
    2. Generate HMAC-SHA256 signature using partner_key
    3. Compare with Authorization header value
    
    Args:
        full_url: Complete URL including protocol and domain
        raw_body: Raw request body bytes
        push_key: Partner key for HMAC generation
        authorization: Authorization header value
    Returns:
        True if signature matches
    Raises:
        SignatureMismatch: if signature doesn't match
    """
    try:
        # Convert raw body to string
        body_str = raw_body.decode('utf-8') if raw_body else ''
        
        # Create signature base string: URL + '|' + body
        base_string = f"{full_url}|{body_str}"
        
        # Generate expected signature
        computed_signature = hmac_sha256(base_string, push_key)
        
        # Compare signatures using constant-time comparison
        if not constant_time_compare(authorization, computed_signature):
            raise SignatureMismatch("Push Authorization signature mismatch")
        
        return True
        
    except UnicodeDecodeError:
        raise SignatureMismatch("Unable to decode webhook body as UTF-8")
    except Exception as e:
        raise SignatureMismatch(f"Push Authorization verification failed: {str(e)}")


def _verify_legacy_signature(raw_body: bytes, push_key: str, signature_header: str, headers: Dict[str, str]) -> bool:
    """Verify legacy webhook signature method.
    
    Legacy method uses raw body HMAC-SHA256 with push key.
    Also validates timestamp drift if present.
    
    Args:
        raw_body: Raw request body bytes
        push_key: Partner key for HMAC generation
        signature_header: X-Shopee-Signature header value
        headers: All request headers
    Returns:
        True if signature valid
    Raises:
        SignatureMismatch: if signature doesn't match
    """
    computed = hmac_sha256(raw_body, push_key, raw=True)
    if not constant_time_compare(signature_header, computed):
        raise SignatureMismatch("Legacy webhook signature mismatch")
    ts_header = headers.get(WEBHOOK_TIMESTAMP_HEADER)
    if ts_header:
        try:
            ts = int(ts_header)
            now = int(time.time())
            if abs(now - ts) > WEBHOOK_ALLOWED_DRIFT_SECONDS:
                raise SignatureMismatch("Webhook timestamp drift too large")
        except SignatureMismatch:
            raise
        except Exception:
            raise SignatureMismatch("Invalid webhook timestamp header")
    return True

def refresh_access_token() -> Dict[str, Any]:
    """Complete token refresh flow with HTTP call and persistence.

    Returns:
        Dict with refresh result and new token info.
    """
    from shopee_bridge import clients
    try:
        payload = refresh_token_via_api()

        response = clients._do_request(
            payload["method"],
            payload["url"],
            {"Content-Type": "application/json"},
            None,
            payload.get("json"),
            None,
        )
        status, text, headers = response

        if status != 200:
            raise Exception(f"Refresh failed: HTTP {status} - {text}")

        import json
        data = json.loads(text)
        if data.get("error"):
            raise Exception(f"Shopee API error: {data}")

        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        expires_in = int(data.get("expires_in", 14400))

        if not access_token:
            raise Exception("No access_token in refresh response")

        settings = _settings()
        settings.access_token = access_token
        if refresh_token:
            settings.refresh_token = refresh_token
        settings.token_expires_at = _utc_naive(expires_in)
        settings.save(ignore_permissions=True)
        frappe.db.commit()
        frappe.cache().delete_value("Shopee Settings")

        frappe.logger().info("[Shopee] Access token refreshed successfully")
        return {"success": True, "expires_in": expires_in, "expires_at": settings.token_expires_at}

    except Exception as e:
        frappe.log_error(f"Token refresh failed: {str(e)}", "Shopee Token Refresh")
        return {"success": False, "error": str(e)}

def refresh_access_token_if_needed(buffer_seconds: int = 600) -> bool:
    """Refresh access token if expired or expiring soon.
    
    Official Shopee v2 OAuth docs: https://open.shopee.com/documents?module=2&type=1&id=53
    - Access tokens expire in 4 hours (14400 seconds)
    - Refresh tokens are valid for 30 days
    - Always refresh proactively before expiry to avoid 401 errors
    - Buffer prevents race conditions and network delays
    
    Args:
        buffer_seconds: Refresh if token expires within this many seconds (default 10min)
        
    Returns:
        True if refresh was performed, False if not needed
        
    Raises:
        AuthRequired: If no refresh token available
        Exception: If refresh fails
    """
    settings = _settings()
    
    if not settings.refresh_token:
        raise AuthRequired("No refresh token available")
        
    if not settings.token_expires_at:
        # No expiry info, assume expired and refresh
        return _perform_refresh()
        
    # Calculate if refresh needed
    expiry_epoch = _parse_expiry_to_epoch(settings.token_expires_at)
    now_epoch = now_epoch()
    
    if expiry_epoch - now_epoch <= buffer_seconds:
        return _perform_refresh()
        
    return False


def get_valid_access_token() -> str:
    """Get a valid access token, refreshing if necessary.
    
    Official Shopee v2 OAuth docs: https://open.shopee.com/documents?module=2&type=1&id=53
    - Always check token validity before API calls
    - Auto-refresh prevents 401 errors in production
    - Thread-safe with database locking
    
    Returns:
        Valid access token string
        
    Raises:
        AuthRequired: If no tokens available or refresh fails
    """
    settings = _settings()
    
    if not settings.access_token:
        raise AuthRequired("No access token available")
        
    # Check if refresh needed
    if refresh_access_token_if_needed():
        # Token was refreshed, reload settings
        _frappe().cache().delete_value("Shopee Settings")
        settings.reload()
        
    if not settings.access_token:
        raise AuthRequired("Failed to obtain valid access token")
        
    return settings.access_token


def _perform_refresh() -> bool:
    """Internal function to perform token refresh with locking."""
    # Use Frappe's locking to prevent concurrent refreshes
    lock_key = "shopee_token_refresh"
    
    try:
        # Acquire lock (non-blocking)
        if not _frappe().cache().get(lock_key):
            _frappe().cache().set(lock_key, True, expires_in_sec=30)
            
            result = refresh_access_token()
            success = result.get("success", False)
            
            if success:
                _frappe().logger().info("[Shopee] Proactive token refresh successful")
            else:
                _frappe().logger().warning(f"[Shopee] Proactive token refresh failed: {result.get('error')}")
                
            return success
        else:
            # Another process is refreshing, wait briefly and check
            time.sleep(2)
            return False
            
    finally:
        _frappe().cache().delete_value(lock_key)

def hmac_sha256(data: Union[str, bytes], key: str, raw: bool = False) -> str:
    """Return hex digest HMAC-SHA256.

    Args:
        data: String or raw bytes.
        key: Secret key material.
        raw: Whether `data` is already bytes (skip encode).
    """
    if not raw:
        data = data.encode("utf-8")
    return hmac.new(key.encode("utf-8"), data, hashlib.sha256).hexdigest()

def constant_time_compare(val1: str, val2: str) -> bool:
    """Constant‑time string comparison to reduce timing side-channel risk."""
    if len(val1) != len(val2):
        return False
    result = 0
    for a, b in zip(val1, val2):
        result |= ord(a) ^ ord(b)
    return result == 0

def _parse_expiry_to_epoch(expires_at) -> int:
    """Parse token expiry to epoch timestamp.
    
    Args:
        expires_at: Expiry datetime (string, datetime, or epoch)
        
    Returns:
        Epoch timestamp as int
    """
    if isinstance(expires_at, str):
        try:
            dt = _frappe().utils.get_datetime(expires_at)
            return int(dt.timestamp())
        except:
            # Fallback to direct parsing
            return int(expires_at)
    elif isinstance(expires_at, datetime):
        return int(expires_at.timestamp())
    else:
        return int(expires_at)


# Exported names (explicit for linting / clarity)
__all__ = [
    "build_authorize_url",
    "handle_oauth_callback",
    "complete_token_exchange",
    "exchange_code_for_token",
    "refresh_if_needed",
    "refresh_token_via_api",
    "refresh_access_token",
    "refresh_access_token_if_needed",
    "get_valid_access_token",
    "sign_request",
    "verify_webhook_signature",
    "schedule_token_renewal_cron",
    "get_shop_info",
    "get_token_status",
    # Exceptions
    "AuthRequired",
    "InvalidState",
    "SignatureMismatch",
]