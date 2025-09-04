from typing import List, Dict, Any, Union, Optional
import time
import hmac
import hashlib
import urllib.parse
import secrets
import frappe

"""Shopee Bridge authentication utilities.

This module ONLY prepares data structures (URLs, signed parameters, payloads) and manipulates
stored credentials in the Single doctype "Shopee Settings". It MUST NOT perform outbound HTTP
requests; network I/O belongs in `clients.py`.

Implemented capabilities:
- OAuth v2 helper flows (build authorize URL, exchange code, refresh flow heuristic)
- HMAC-SHA256 request signing per Shopee v2 rule:
        signature_base = partner_id + path + timestamp + access_token + shop_id
        signature = hex(HMAC_SHA256(signature_base, partner_key))
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


PROD_BASE_URL = "https://partner.shopeemobile.com"
TEST_BASE_URL = "https://partner.test-stable.shopeemobile.com"

OAUTH_AUTHORIZE_PATH = "/api/v2/shop/auth_partner"
OAUTH_TOKEN_PATH = "/api/v2/auth/token/get"
OAUTH_REFRESH_PATH = "/api/v2/auth/token/refresh"

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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _settings() -> frappe.model.document.Document:
    """Get cached Shopee Settings doc.

    Returns:
        The cached document instance.
    Raises:
        AuthRequired: if the document cannot be loaded (app not yet installed).
    """
    try:
        return frappe.get_cached_doc("Shopee Settings")
    except Exception as exc:  # pragma: no cover - defensive guard
        raise AuthRequired("Shopee Settings not configured") from exc


def _base_url(env: str) -> str:
    return PROD_BASE_URL if env == "Production" else TEST_BASE_URL


def _mask_secret(value: Optional[str], show: int = 4) -> str:
    if not value:
        return "<empty>"
    if len(value) <= show:
        return "*" * len(value)
    return value[:show] + "…" + "*" * (len(value) - show)


def _cache_set(key: str, val: str, ttl: int):
    frappe.cache().set_value(key, val, expires_in_sec=ttl)


def _cache_get(key: str) -> Optional[str]:
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
    frappe.cache().delete_value(STATE_CACHE_PREFIX + state)

def get_shop_info() -> Dict[str, Any]:
    """Return lightweight shop info placeholder.

    Real implementation belongs in a client performing HTTP call. Kept here only as a
    convenience placeholder so other code can probe readiness.
    """
    # TODO: Replace with real shop.get request via clients.py
    settings = _settings()
    return {
        "shop_id": getattr(settings, "shop_id", None),
        "environment": settings.environment,
        "has_token": bool(getattr(settings, "access_token", None)),
    }

def build_authorize_url(scopes: List[str]) -> str:
    """Build the Shopee OAuth v2 authorization URL.

    Shopee expects partner_id, redirect URL, and optional scope CSV. We also add a
    random `state` token stored in cache to mitigate CSRF / replay.

    Args:
        scopes: List of scopes requested.
    Returns:
        Fully composed HTTPS URL for the authorization step.
    """
    settings = _settings()
    partner_id = settings.partner_id
    redirect_url = settings.redirect_url
    base_url = _base_url(settings.environment)
    scope_str = ",".join(scopes)
    state = _build_state()
    qs = urllib.parse.urlencode(
        {
            "partner_id": partner_id,
            "redirect": redirect_url,
            "scope": scope_str,
            "state": state,
        }
    )
    return f"{base_url}{OAUTH_AUTHORIZE_PATH}?{qs}"

def handle_oauth_callback(params: Dict[str, Any]) -> None:
    """Process the OAuth callback parameters.

    Steps:
    1. Validate required params (code, shop_id, state)
    2. Validate & consume `state` token
    3. Build token exchange payload (returned for HTTP layer) & persist placeholders

    NOTE: We store only *placeholder* token data here; the actual HTTP response parsing
    (access_token, refresh_token, expires_in) must be done in clients then call a small
    utility (TODO) to persist real values.

    Args:
        params: Dict of query parameters returned by Shopee to redirect URL.
    Raises:
        InvalidState: if validation fails.
    """
    code = params.get("code")
    shop_id = params.get("shop_id")
    state = params.get("state")
    if not code or not shop_id:
        raise InvalidState("Missing code or shop_id in callback params")
    _validate_state(state)
    # Prepare exchange payload (for client to execute real HTTP)
    exchange_code_for_token(code, shop_id)  # side effect free (returns payload)
    # Persist preliminary context
    settings = _settings()
    settings.shop_id = shop_id
    settings.last_auth_code = code  # for audit / debugging
    settings.save(ignore_permissions=True)
    frappe.cache().delete_value("Shopee Settings")

def exchange_code_for_token(code: str, shop_id: Union[str, int]) -> Dict[str, Any]:
    """Produce payload for exchanging an authorization code for tokens.

    Shopee signing pattern for token exchange includes code & shop_id appended to the
    canonical path string.

    Args:
        code: Authorization code received from redirect.
        shop_id: Target shop identifier.
    Returns:
        Dict representing the JSON body + metadata needed for HTTP layer.
    """
    settings = _settings()
    partner_id = settings.partner_id
    partner_key = settings.get_password("partner_key")
    timestamp = int(time.time())
    base_string = str(partner_id) + OAUTH_TOKEN_PATH + str(timestamp) + code + str(shop_id)
    sign = hmac_sha256(base_string, partner_key)
    return {
        "method": "POST",
        "url": f"{_base_url(settings.environment)}{OAUTH_TOKEN_PATH}",
        "json": {
            "partner_id": partner_id,
            "code": code,
            "shop_id": int(shop_id),
            "timestamp": timestamp,
            "sign": sign,
        },
        "meta": {"signature_base": base_string},
    }

def refresh_if_needed(buffer_seconds: int = 600) -> bool:
    """Heuristically decide whether to refresh the token soon.

    Does NOT perform network I/O; only returns whether a refresh payload was produced.
    The caller (scheduler / client) should then execute the HTTP request using the
    payload from `refresh_token_via_api()` and persist new tokens.

    Args:
        buffer_seconds: Trigger refresh if (expiry - now) < buffer_seconds.
    Returns:
        True if a refresh SHOULD happen now, else False.
    """
    settings = _settings()
    expiry = int(getattr(settings, "token_expiry", 0) or 0)
    if not expiry:
        return False
    now = int(time.time())
    if expiry - now < buffer_seconds:
        # Prepare payload (not used here, but side effects minimal)
        refresh_token_via_api()
        return True
    return False

def refresh_token_via_api() -> Dict[str, Any]:
    """Produce payload for token refresh.

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
    base_string = (
        str(partner_id) + OAUTH_REFRESH_PATH + str(timestamp) + refresh_token + str(shop_id)
    )
    sign = hmac_sha256(base_string, partner_key)
    return {
        "method": "POST",
        "url": f"{_base_url(settings.environment)}{OAUTH_REFRESH_PATH}",
        "json": {
            "partner_id": partner_id,
            "shop_id": int(shop_id),
            "refresh_token": refresh_token,
            "timestamp": timestamp,
            "sign": sign,
        },
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
        raise AuthRequired("Missing partner_id / access_token / shop_id")
    partner_key = settings.get_password("partner_key")
    timestamp = int(time.time())
    base_string = f"{partner_id}{path}{timestamp}{access_token}{shop_id}"
    signature = hmac_sha256(base_string, partner_key)
    # Base query pieces required by Shopee
    base_qs = {
        "partner_id": partner_id,
        "timestamp": timestamp,
        "access_token": access_token,
        "shop_id": shop_id,
        "sign": signature,
    }
    # Merge user params (user params should not override required ones)
    merged = {**params, **base_qs}
    qs = urllib.parse.urlencode(merged, doseq=True)
    url = f"{_base_url(settings.environment)}{path}?{qs}"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    return {"url": url, "headers": headers, "meta": {"timestamp": timestamp, "signature_base": base_string}}

def verify_webhook_signature(
    path: str,  # kept for future parity / auditing
    raw_body: bytes,
    headers: Dict[str, str],
    push_key: str
) -> bool:
    """Validate a Shopee webhook request.

    Current Shopee webhook signing (v2) uses the raw body HMAC-SHA256 with the push key.
    We optionally validate timestamp drift when header is present.

    Args:
        path: Original request path (not currently used for signature – reserved for future use).
        raw_body: Raw request body bytes.
        headers: Incoming HTTP headers (case-sensitive keys expected as provided by Frappe).
        push_key: Shared secret key from Shopee dashboard (test or live push key).
    Returns:
        True if signature (& optional timestamp) valid.
    Raises:
        SignatureMismatch: on any mismatch or malformed header.
    """
    signature_header = headers.get(WEBHOOK_SIGNATURE_HEADER)
    if not signature_header:
        raise SignatureMismatch(f"Missing {WEBHOOK_SIGNATURE_HEADER} header")
    computed = hmac_sha256(raw_body, push_key, raw=True)
    if not constant_time_compare(signature_header, computed):
        raise SignatureMismatch("Webhook signature mismatch")
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

def cron_refresh_job():  # pragma: no cover - scheduled job wrapper
    """Background job wrapper invoked by the scheduler (no arguments)."""
    try:
        if refresh_if_needed():
            frappe.logger().info("[Shopee] Token refresh suggested by cron")
    except Exception as exc:  # swallow to avoid job crash
        frappe.logger().warning(f"[Shopee] cron refresh error: {exc}")


def schedule_token_renewal_cron() -> Dict[str, Any]:
    """Create or ensure a Scheduled Job Type for proactive token renewal.

    Strategy: Hourly job invokes `cron_refresh_job` which itself decides whether refresh is needed.
    Returns a dict describing the upsert action.
    """
    job_method = "shopee_bridge.auth.cron_refresh_job"
    existing = frappe.db.exists("Scheduled Job Type", {"method": job_method})
    if existing:
        return {"status": "exists", "name": existing}
    try:
        doc = frappe.get_doc({
            "doctype": "Scheduled Job Type",
            "method": job_method,
            "frequency": "Hourly",
            "stopped": 0,
        }).insert(ignore_permissions=True)
        return {"status": "created", "name": doc.name}
    except Exception as exc:  # pragma: no cover
        frappe.log_error(message=str(exc), title="Shopee schedule_token_renewal_cron failure")
        return {"status": "error", "error": str(exc)}

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


# Exported names (explicit for linting / clarity)
__all__ = [
    "build_authorize_url",
    "handle_oauth_callback",
    "exchange_code_for_token",
    "refresh_if_needed",
    "refresh_token_via_api",
    "sign_request",
    "verify_webhook_signature",
    "schedule_token_renewal_cron",
    "get_shop_info",
    # Exceptions
    "AuthRequired",
    "InvalidState",
    "SignatureMismatch",
]