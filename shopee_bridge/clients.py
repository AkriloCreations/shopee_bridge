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

from typing import Any, Callable, Dict, Optional, Union, List, Iterator
import json as _json
import time
import traceback
import hashlib

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


class ShopeeAPIError(Exception):
	"""Base exception for Shopee API errors."""
	
	def __init__(self, message: str, status_code: Optional[int] = None, response_data: Optional[Dict] = None):
		super().__init__(message)
		self.status_code = status_code
		self.response_data = response_data or {}


class ShopeeRateLimitError(ShopeeAPIError):
	"""Raised when rate limit is exceeded."""
	pass


class ShopeeAuthError(ShopeeAPIError):
	"""Raised when authentication fails."""
	pass


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


def _map_error(status: int, data: Dict) -> ShopeeAPIError:
	"""Map HTTP status and response data to appropriate exception."""
	error_msg = data.get("message") or data.get("error") or f"HTTP {status}"
	
	if status == 429:
		return ShopeeRateLimitError(error_msg, status, data)
	elif status == 401:
		return ShopeeAuthError(error_msg, status, data)
	elif status == 403:
		return ShopeeAuthError("Forbidden - check permissions", status, data)
	elif status == 404:
		return ShopeeAPIError("Resource not found", status, data)
	elif status >= 500:
		return ShopeeAPIError(f"Server error: {error_msg}", status, data)
	else:
		return ShopeeAPIError(error_msg, status, data)


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
			
			data = _parse_body(text)
			last_error = _map_error(status, data)
			break
			
		except Exception as exc:
			if attempt <= MAX_RETRIES:
				delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS)-1)]
				_log_short(f"[Shopee] network retry {attempt}/{MAX_RETRIES} delay={delay}s path={path}")
				time.sleep(delay)
				continue
			last_error = ShopeeAPIError(f"HTTP error: {exc}")
			break
	
	frappe.log_error(message=str(last_error), title="Shopee HTTP error")
	raise last_error


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


def paginate_get(path: str, params: Dict[str, Any], page_size: int = 100, max_pages: int = 50) -> Iterator[Dict[str, Any]]:
	"""Paginate through Shopee API results.
	
	Args:
		path: API endpoint path
		params: Base parameters
		page_size: Items per page
		max_pages: Maximum pages to fetch
		
	Yields:
		Individual result items
		
	Raises:
		ShopeeAPIError: On pagination errors
	"""
	params = params.copy()
	params["page_size"] = page_size
	cursor = None
	page_count = 0
	
	while page_count < max_pages:
		if cursor:
			params["cursor"] = cursor
		
		try:
			resp = http_get(path, params)
			data = resp.get("response") or resp
			
			# Extract items with fallback
			items = (
				data.get("order_list") or 
				data.get("return_list") or 
				data.get("list") or 
				data.get("items") or 
				[]
			)
			
			if not items:
				break
				
			for item in items:
				if item:  # Skip empty items
					yield item
			
			# Check for more pages
			more = data.get("more", False)
			cursor = data.get("next_cursor") or data.get("cursor")
			
			if not more or not cursor:
				break
				
			page_count += 1
			
		except ShopeeRateLimitError:
			# Wait longer for rate limits
			time.sleep(5)
			continue
		except ShopeeAPIError as e:
			frappe.log_error(f"Pagination error at page {page_count}: {e}", "Shopee Pagination")
			break


def batch_request(path: str, items: List[str], batch_size: int = 50, method: str = "GET", **kwargs) -> Iterator[Dict[str, Any]]:
	"""Batch requests for endpoints that support multiple items.
	
	Args:
		path: API endpoint path
		items: List of item identifiers
		batch_size: Items per batch
		method: HTTP method
		**kwargs: Additional parameters
		
	Yields:
		Response data for each batch
		
	Raises:
		ShopeeAPIError: On batch request errors
	"""
	for i in range(0, len(items), batch_size):
		batch = items[i:i + batch_size]
		
		try:
			if method == "GET":
				params = kwargs.get("params", {}).copy()
				params["order_sn_list"] = ",".join(batch) if len(batch) > 1 else batch[0]
				resp = http_get(path, params)
			else:
				# For POST requests
				data = kwargs.get("json", {}).copy()
				data["order_sn_list"] = batch
				resp = http_post(path, json=data)
			
			data = resp.get("response") or resp
			results = (
				data.get("order_list") or 
				data.get("orders") or 
				data.get("items") or 
				[data]
			)
			
			for result in results:
				if result:  # Skip empty results
					yield result
					
		except ShopeeAPIError as e:
			frappe.log_error(f"Batch request error for items {batch[:3]}...: {e}", "Shopee Batch Request")
			# Continue with next batch instead of failing completely
			continue


def log_request(path: str, params: Dict[str, Any], response: Dict[str, Any], duration: float):
	"""Log API request for debugging and monitoring."""
	try:
		# Create hash of request for deduplication
		request_hash = hashlib.sha1(
			f"{path}:{_json.dumps(params, sort_keys=True)}".encode()
		).hexdigest()
		
		frappe.get_doc({
			"doctype": "Shopee Sync Log",
			"job": "api_request",
			"key": request_hash,
			"status": "ok" if response.get("_status", 200) < 400 else "fail",
			"payload_hash": request_hash,
			"message": f"API call to {path}",
			"started_at": frappe.utils.now_datetime(),
			"ended_at": frappe.utils.now_datetime(),
			"meta_json": _json.dumps({
				"path": path,
				"duration": duration,
				"status": response.get("_status"),
				"params_count": len(params)
			})
		}).insert(ignore_permissions=True)
		
	except Exception as e:
		frappe.logger().warning(f"Failed to log API request: {e}")


__all__ = ["http_get", "http_post", "rotate_on_401", "paginate_get", "batch_request", "log_request", 
		   "ShopeeAPIError", "ShopeeRateLimitError", "ShopeeAuthError"]
