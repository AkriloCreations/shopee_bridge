"""Shopee API HTTP client helpers.

Abstractions:
	http_get / http_post build signed request metadata via `auth.sign_request` then
	perform the actual HTTP call with retry + 401 refresh fallback provided by
	`rotate_on_401`.

Design notes:
	- No business mapping here; only raw HTTP mechanics.
	- Lightweight retry for 429 / 5xx (max 2 retries: delays 1s then 3s).
	- 401 refresh sequence: obtain refresh payload via auth.refresh_token_via_api(); the
	  actual network call that exchanges refresh token SHOULD be done elsewhere and
	  persisted (TODO). Here we only demonstrate logical flow and re-sign request after
	  refresh persistence.
	- Secrets are never logged; only truncated identifiers.

Assumptions:
	- `requests` library is available in the bench environment. If not, fallback to
	  frappe's integration utils.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Union
import json as _json
import time
import traceback

import frappe

try:  # Prefer requests
	import requests  # type: ignore
	_HAS_REQUESTS = True
except Exception:  # pragma: no cover - fallback path
	from frappe.integrations.utils import (  # type: ignore
		make_get_request as _frappe_get,
		make_post_request as _frappe_post,
	)
	_HAS_REQUESTS = False

from . import auth

DEFAULT_TIMEOUT = 20  # seconds
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 2
RETRY_DELAYS = [1, 3]  # seconds


def _do_request(method: str, url: str, headers: Dict[str, str], params: Dict[str, Any] | None, json: Dict[str, Any] | None, files: Dict[str, Any] | None) -> tuple[int, str, Dict[str, Any]]:
    """Execute raw HTTP request using requests or frappe fallback."""
    if _HAS_REQUESTS:
        try:
            resp = requests.request(
                method,
                url,
                headers=headers,
                params=params if params else None,
                json=json if json is not None else None,
                files=files,
                timeout=DEFAULT_TIMEOUT,
            )
            return resp.status_code, resp.text, dict(resp.headers)
        except Exception as exc:
            frappe.log_error(f"Shopee HTTP {method} error: {exc}")
            raise
    else:
        try:
            if method == "GET":
                data = _frappe_get(url, headers=headers, params=params or {})
                return 200, _json.dumps(data), {}
            else:
                data = _frappe_post(url, headers=headers, data=json or {})
                return 200, _json.dumps(data), {}
        except Exception as exc:
            frappe.log_error(f"Shopee HTTP fallback error: {exc}")
            raise


def _parse_body(text: str) -> Dict[str, Any]:
    try:
        return _json.loads(text)
    except Exception:
        return {"raw": text}


def _retryable(status: int) -> bool:
    return status in RETRY_STATUSES


def _log_short(msg: str):
    frappe.logger().info(msg)


def _execute_with_retry(method: str, path: str, params: Dict[str, Any], json: Dict[str, Any] | None, files: Dict[str, Any] | None) -> Dict[str, Any]:
    attempt = 0
    last_error = None
    while True:
        attempt += 1
        try:
            signed = auth.sign_request(path, params.copy(), None)
            url = signed["url"]
            headers = signed["headers"]
            status, text, resp_headers = _do_request(method, url, headers, params={}, json=json, files=files)
            if status == 401:
                return {"_status": status, "_text": text, "_headers": resp_headers}
            if 200 <= status < 300:
                data = _parse_body(text)
                data["_status"] = status
                return data
            if _retryable(status) and attempt <= MAX_RETRIES:
                delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS)-1)]
                _log_short(f"[Shopee] retry {attempt}/{MAX_RETRIES} status={status} delay={delay}s path={path}")
                time.sleep(delay)
                continue
            last_error = f"HTTP {status} body={text[:300]}"
            break
        except Exception as exc:
            last_error = f"HTTP error: {exc}"
            break
    frappe.log_error(message=last_error, title="Shopee HTTP error")
    raise frappe.ValidationError(last_error)


def http_get(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Perform signed GET request."""
    return rotate_on_401(lambda: _execute_with_retry("GET", path, params, None, None))


def http_post(path: str, json: Dict[str, Any] | None = None, files: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Perform signed POST request."""
    return rotate_on_401(lambda: _execute_with_retry("POST", path, {}, json, files))


def rotate_on_401(send_callable: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    """Execute a send callable; on 401 attempt one refresh cycle then retry."""
    first = send_callable()
    if first.get("_status") != 401:
        return first
    _log_short("[Shopee] 401 encountered; attempting token refresh heuristic")
    try:
        # Only refresh if needed, and persist new tokens if refreshed
        if auth.refresh_if_needed():
            result = auth.refresh_access_token()
            if not result.get("success"):
                frappe.logger().warning(f"[Shopee] Token refresh failed: {result.get('error')}")
            else:
                frappe.logger().info("[Shopee] Token refresh successful via rotate_on_401")
        frappe.cache().delete_value("Shopee Settings")
    except Exception as exc:
        frappe.log_error(f"Shopee refresh error: {exc}")
        return first
    second = send_callable()
    return second


__all__ = ["http_get", "http_post", "rotate_on_401"]
