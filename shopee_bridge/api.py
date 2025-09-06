"""Public HTTP API endpoints (thin wrappers only).

Each function:
 - Performs minimal input validation / coercion.
 - Delegates business logic to modules under auth.* or jobs.*.
 - Never raises to the client; always returns a JSON‑serializable dict with either
   an "ok" flag / result data or an "error" string.
 - Contains no outbound HTTP logic or Shopee domain logic (kept in other modules).

Webhook endpoints (`webhook_live`, `webhook_test`) implement a fast ingest pattern:
 - Verify signature using the appropriate push key.
 - Derive an idempotency key (stable hash) from core event attributes.
 - Insert a Shopee Webhook Inbox row (status=queued, signature_valid=True/False).
 - Enqueue async processing (short queue) and return immediately.

NOTE: This file must remain intentionally thin to ease maintenance, testing, and
security auditing. Heavy logic belongs in dedicated service / job modules.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import json, hashlib
import frappe

from shopee_bridge import auth, helpers


def _result(data: Dict[str, Any], ok: bool = True) -> Dict[str, Any]:
	data.setdefault("ok", ok)
	return data


def _error(err: Exception | str) -> Dict[str, Any]:
	msg = str(err)
	frappe.log_error(message=msg, title="Shopee API Error")
	return {"ok": False, "error": msg}


def _get_settings():
	try:
		return frappe.get_cached_doc("Shopee Settings")
	except Exception as exc:  # pragma: no cover
		raise frappe.ValidationError("Shopee Settings not configured") from exc


@frappe.whitelist()
def connect_to_shopee(scopes: Optional[List[str]] = None) -> Dict[str, Any]:
	"""Return OAuth authorize URL.

	Args:
		scopes: Optional list of scopes.
	"""
	try:
		scopes = scopes or [
			"shop.basic.info",
			"order",
			"payment",
			"returns",
			"logistics",
		]
		if not isinstance(scopes, (list, tuple)):
			raise ValueError("scopes must be list[str] or None")
		scopes = [s for s in scopes if s]
		url = auth.build_authorize_url(scopes)
		return _result({"url": url})
	except Exception as e:  # pragma: no cover - defensive
		return _error(e)


@frappe.whitelist()
def oauth_callback(**params) -> Dict[str, Any]:
	"""
	Endpoint untuk menerima callback dari Shopee OAuth.
	Proses simpan access_token, refresh_token, shop_id, dll dilakukan di auth.handle_oauth_callback.
	Semua logic tetap di auth.py, api.py hanya wrapper tipis.
	"""
	try:
		auth.handle_oauth_callback(params)
		return _result({"message": "callback processed"})
	except Exception as e:
		return _error(e)


@frappe.whitelist()
def test_shopee_connection() -> Dict[str, Any]:
	"""Return minimal shop info to test connectivity."""
	try:
		info = auth.get_shop_info()
		return _result({"shop": info})
	except Exception as e:
		return _error(e)


def _derive_idempotency(payload: Dict[str, Any]) -> str:
	# Pick common distinguishing fields (fallback to full payload hash)
	parts: List[str] = []
	for key in ["event_type", "order_sn", "return_sn", "status", "update_time"]:
		val = payload.get(key)
		if val is not None:
			parts.append(str(val))
	if not parts:
		parts.append(json.dumps(payload, sort_keys=True))
	raw = "|".join(parts)
	return hashlib.sha1(raw.encode("utf-8")).hexdigest()  # noqa: S324 (non‑crypto uniqueness ok)


def _process_webhook(source_env: str) -> Dict[str, Any]:
	req = frappe.request
	raw_body: bytes = req.data or b""
	headers = {k: v for k, v in (req.headers or {}).items()}
	try:
		settings = _get_settings()
		enabled_flag = (
			settings.webhook_live_enabled if source_env == "live" else settings.webhook_test_enabled
		)
		if not enabled_flag:
			raise frappe.PermissionError(f"Webhook {source_env} disabled")
		push_key_field = (
			"live_partner_push_key" if source_env == "live" else "test_partner_push_key"
		)
		push_key = settings.get_password(push_key_field)
		if not push_key:
			raise frappe.ValidationError(f"Missing {push_key_field}")
		# Verify signature (raises on mismatch)
		# Build full URL for Push Authorization verification
		full_url = f"{req.scheme}://{req.host}{req.path}"
		auth.verify_webhook_signature(
			path=req.path, raw_body=raw_body, headers=headers, push_key=push_key, full_url=full_url
		)
		signature_valid = 1
	except Exception as sig_exc:
		# Signature invalid or config issue: still record inbox for audit (signature_valid=0)
		signature_valid = 0
		sig_err = str(sig_exc)
	# Parse JSON payload
	try:
		payload_json = raw_body.decode("utf-8") if raw_body else "{}"
		payload = json.loads(payload_json or "{}")
		print("payload", payload)
	except Exception:
		payload = {}
		payload_json = "{}"
	# Compute idempotency key
	idem_key = _derive_idempotency(payload)
	# Insert inbox doc
	try:
		inbox = frappe.get_doc({
			"doctype": "Shopee Webhook Inbox",
			"event_type": payload.get("event_type") or payload.get("type") or "unknown",
			"source_env": source_env,
			"idempotency_key": idem_key,
			"signature_valid": signature_valid,
			"status": "queued",
			"payload_hash": hashlib.sha1(payload_json.encode("utf-8")).hexdigest(),  # noqa: S324
			"payload_json": payload_json,
		}).insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception as ins_exc:
		return _error(ins_exc)
	# Enqueue async processing (even if signature invalid we may want to inspect)
	try:
		frappe.enqueue(
			"shopee_bridge.jobs.process_webhook.run",
			inbox=inbox.name,
			queue="short",
		)
	except Exception as q_exc:  # pragma: no cover
		frappe.log_error(message=str(q_exc), title="Shopee Webhook Enqueue Error")
	resp = {"inbox": inbox.name, "idempotency_key": idem_key, "signature_valid": bool(signature_valid)}
	if signature_valid == 0:
		resp["warning"] = sig_err  # expose minimal diagnostic
	return _result(resp)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def webhook_live() -> Dict[str, Any]:
	"""Live webhook endpoint."""
	return _process_webhook("live")


@frappe.whitelist(allow_guest=True, methods=["POST"])
def webhook_test() -> Dict[str, Any]:
	"""Test webhook endpoint."""
	return _process_webhook("test")


@frappe.whitelist()
def sync_orders_api(minutes: int = 15) -> Dict[str, Any]:
	"""Trigger incremental order sync job synchronously (thin).

	Args:
		minutes: lookback window in minutes.
	"""
	try:
		minutes = int(minutes)
		if minutes <= 0:
			raise ValueError("minutes must be > 0")
		from .jobs import sync_orders  # local import to keep module light

		res = sync_orders.run(minutes=minutes)
		return _result({"sync": res or {}})
	except Exception as e:
		return _error(e)


@frappe.whitelist()
def sync_finance_api() -> Dict[str, Any]:
	"""Trigger finance sync job (hourly escrow batch)."""
	try:
		from .jobs import sync_finance  # local import

		res = sync_finance.run(hours=1)
		return _result({"sync": res or {}})
	except Exception as e:
		return _error(e)


# === ORDERS API ===

@frappe.whitelist()
def get_order(order_sn: str) -> Dict[str, Any]:
	"""Get single order details from Shopee."""
	try:
		from shopee_bridge.services import orders
		order_data = orders.get_order_details(order_sn)
		return _result({"order": order_data})
	except Exception as e:
		return _error(e)


@frappe.whitelist()
def sync_order(order_sn: str) -> Dict[str, Any]:
	"""Sync specific order from Shopee to ERPNext."""
	try:
		from shopee_bridge.services import orders
		result = orders.sync_single_order(order_sn)
		return _result({"sync_result": result})
	except Exception as e:
		return _error(e)


@frappe.whitelist()
def update_order_status(order_sn: str, status: str) -> Dict[str, Any]:
	"""Update order status in Shopee."""
	try:
		from shopee_bridge.services import orders
		result = orders.update_order_status(order_sn, status)
		return _result({"updated": result})
	except Exception as e:
		return _error(e)


# === LOGISTICS API ===

@frappe.whitelist()
def get_shipping_info(order_sn: str) -> Dict[str, Any]:
	"""Get shipping information for an order."""
	try:
		from .services import logistics
		shipping_data = logistics.get_shipping_info(order_sn)
		return _result({"shipping": shipping_data})
	except Exception as e:
		return _error(e)


@frappe.whitelist()
def sync_shipping_api(minutes: int = 30) -> Dict[str, Any]:
	"""Trigger shipping sync job."""
	try:
		minutes = int(minutes)
		if minutes <= 0:
			raise ValueError("minutes must be > 0")
		from .jobs import sync_shipping
		res = sync_shipping.run(minutes=minutes)
		return _result({"sync": res or {}})
	except Exception as e:
		return _error(e)


@frappe.whitelist()
def update_tracking(order_sn: str, tracking_number: str) -> Dict[str, Any]:
	"""Update tracking number for an order."""
	try:
		from .services import logistics
		result = logistics.update_tracking_number(order_sn, tracking_number)
		return _result({"updated": result})
	except Exception as e:
		return _error(e)


# === RETURNS API ===

@frappe.whitelist()
def get_returns(order_sn: str = None) -> Dict[str, Any]:
	"""Get returns data, optionally filtered by order."""
	try:
		from .services import returns
		returns_data = returns.get_returns_list(order_sn=order_sn)
		return _result({"returns": returns_data})
	except Exception as e:
		return _error(e)


@frappe.whitelist()
def sync_returns_api(minutes: int = 60) -> Dict[str, Any]:
	"""Trigger returns sync job."""
	try:
		minutes = int(minutes)
		if minutes <= 0:
			raise ValueError("minutes must be > 0")
		from .jobs import sync_returns
		res = sync_returns.run(minutes=minutes)
		return _result({"sync": res or {}})
	except Exception as e:
		return _error(e)


@frappe.whitelist()
def process_return(return_sn: str, action: str) -> Dict[str, Any]:
	"""Process return request (approve/reject/refund)."""
	try:
		from .services import returns
		result = returns.process_return_request(return_sn, action)
		return _result({"processed": result})
	except Exception as e:
		return _error(e)


# === FINANCE API ===

@frappe.whitelist()
def get_payout_info(batch_id: str = None) -> Dict[str, Any]:
	"""Get payout/escrow information."""
	try:
		from .services import finance
		payout_data = finance.get_payout_details(batch_id=batch_id)
		return _result({"payout": payout_data})
	except Exception as e:
		return _error(e)


@frappe.whitelist()
def reconcile_bank_api() -> Dict[str, Any]:
	"""Trigger bank reconciliation job."""
	try:
		from .jobs import reconcile_bank
		result = reconcile_bank.run()
		return _result({"reconciliation": result})
	except Exception as e:
		return _error(e)


@frappe.whitelist()
def sync_escrow_batch(batch_id: str) -> Dict[str, Any]:
	"""Sync specific escrow batch."""
	try:
		from .services import finance
		result = finance.sync_escrow_batch(batch_id)
		return _result({"synced": result})
	except Exception as e:
		return _error(e)


# === WEBHOOK API ===

@frappe.whitelist()
def get_webhook_logs(limit: int = 50) -> Dict[str, Any]:
	"""Get recent webhook processing logs."""
	try:
		limit = int(limit)
		if limit <= 0 or limit > 500:
			raise ValueError("limit must be between 1 and 500")
		
		logs = frappe.get_list(
			"Shopee Webhook Inbox",
			fields=["name", "event_type", "source_env", "status", "signature_valid", "creation", "error_message"],
			order_by="creation desc",
			limit=limit
		)
		return _result({"logs": logs})
	except Exception as e:
		return _error(e)


@frappe.whitelist()
def retry_webhook(inbox_name: str) -> Dict[str, Any]:
	"""Manually retry a failed webhook."""
	try:
		from .jobs import process_webhook
		result = process_webhook.run(inbox=inbox_name)
		return _result({"retry_result": result})
	except Exception as e:
		return _error(e)


# === GENERAL UTILITY API ===

@frappe.whitelist()
def get_health_status() -> Dict[str, Any]:
	"""Get overall system health status."""
	try:
		settings = _get_settings()
		
		# Check token validity
		token_valid = bool(settings.access_token and settings.token_expires_at)
		
		# Check recent sync status
		recent_errors = frappe.db.count("Error Log", {
			"creation": [">=", frappe.utils.add_days(frappe.utils.now(), -1)],
			"error": ["like", "%shopee%"]
		})
		
		# Check webhook status
		from datetime import datetime, timedelta
		one_hour_ago = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
		pending_webhooks = frappe.db.count("Shopee Webhook Inbox", {
			"status": ["in", ["queued", "processing"]],
			"creation": [">=", one_hour_ago]
		})
		
		health_data = {
			"token_valid": token_valid,
			"recent_errors": recent_errors,
			"pending_webhooks": pending_webhooks,
			"settings_configured": bool(settings.partner_id and settings.partner_key),
			"timestamp": frappe.utils.now()
		}
		
		return _result({"health": health_data})
	except Exception as e:
		return _error(e)


@frappe.whitelist()
def refresh_token() -> Dict[str, Any]:
	"""Manually refresh OAuth token."""
	try:
		from . import auth
		refreshed = auth.refresh_access_token_if_needed()
		return _result({"token_refreshed": refreshed})
	except Exception as e:
		return _error(e)


@frappe.whitelist()
def audit_orders(days_back: int = 7) -> Dict[str, Any]:
	"""Audit recent orders for data consistency.
	
	Args:
		days_back: Number of days to look back
		
	Returns:
		Audit results
	"""
	try:
		end_time = helpers.epoch_now()
		start_time = end_time - (days_back * 24 * 60 * 60)
		
		from .services import orders
		order_sns = orders.get_order_list(start_time, end_time)
		
		results = {
			"total_orders": len(order_sns),
			"period_days": days_back,
			"start_time": start_time,
			"end_time": end_time,
			"sample_orders": order_sns[:5] if order_sns else []
		}
		
		return _result({"audit": results})
	except Exception as e:
		return _error(e)


@frappe.whitelist()
def debug_webhook_payload(inbox_name: str) -> Dict[str, Any]:
	"""Debug a webhook payload for troubleshooting.
	
	Args:
		inbox_name: Name of webhook inbox record
		
	Returns:
		Payload details
	"""
	try:
		inbox = frappe.get_doc("Shopee Webhook Inbox", inbox_name)
		payload = json.loads(inbox.payload_json)
		
		debug_info = {
			"inbox_name": inbox_name,
			"event_type": inbox.event_type,
			"source_env": inbox.source_env,
			"signature_valid": inbox.signature_valid,
			"status": inbox.status,
			"attempts": inbox.attempts,
			"payload_keys": list(payload.keys()),
			"payload_size": len(inbox.payload_json),
			"created": inbox.creation,
			"processed_at": inbox.processed_at
		}
		
		return _result({"debug": debug_info, "payload": payload})
	except Exception as e:
		return _error(e)


@frappe.whitelist()
def sync_recent_orders(hours: int = 24, days: int | None = None) -> dict:
	"""Sync recent orders."""
	from shopee_bridge import helpers
	from shopee_bridge.services import orders
	if days is not None:
		hours = days * 24
	since_epoch = helpers.now_epoch() - hours * 3600
	settings = _get_settings()
	host = ""  # not used
	access_token = getattr(settings, "access_token", "")
	shop_id = getattr(settings, "shop_id", 0)
	order_sns = orders.get_order_list(since_epoch, helpers.now_epoch())
	orders_total = len(order_sns)
	escrow_logged = 0
	for sn in order_sns:
		try:
			# Fetch detail
			detail = orders.get_order_details(sn)
			# Fetch and log escrow
			escrow = orders.fetch_and_log_escrow("", host, access_token, shop_id, sn)
			if not escrow.get("error"):
				escrow_logged += 1
		except Exception:
			pass  # ignore errors for count
	return {"orders_total": orders_total, "escrow_logged": escrow_logged}


@frappe.whitelist()
def check_token_health() -> Dict[str, Any]:
	"""Check token health and expiry status.
	
	Returns:
		Token health information
	"""
	try:
		from . import auth
		token_status = auth.get_token_status()
		
		health = {
			"has_access_token": token_status.get("has_access_token", False),
			"has_refresh_token": token_status.get("has_refresh_token", False),
			"expires_at": token_status.get("normalized_expires_at"),
			"seconds_remaining": token_status.get("seconds_remaining"),
			"is_expired": token_status.get("is_expired", True),
			"needs_refresh": token_status.get("needs_refresh", True)
		}
		
		return _result({"health": health})
	except Exception as e:
		return _error(e)


@frappe.whitelist()
def get_sync_logs(limit: int = 20) -> Dict[str, Any]:
	"""Get recent sync logs for monitoring.
	
	Args:
		limit: Maximum number of logs to return
		
	Returns:
		Recent sync logs
	"""
	try:
		logs = frappe.get_list(
			"Shopee Sync Log",
			fields=["name", "job", "status", "message", "started_at", "ended_at", "creation"],
			order_by="creation desc",
			limit=limit
		)
		
		return _result({"logs": logs})
	except Exception as e:
		return _error(e)


@frappe.whitelist()
def debug_sign(path: str) -> dict:
	"""Debug sign a path."""
	from shopee_bridge import helpers
	from shopee_bridge import auth
	signed = auth.sign_request(path, {}, None)
	signature = signed["url"].split("sign=")[1].split("&")[0]
	return {"path": path, "ts": helpers.now_epoch(), "signature": signature}


@frappe.whitelist()
def audit_shopee_orders_for_month(year: int, month: int) -> dict:
	"""Audit orders for a month."""
	from shopee_bridge import helpers
	from shopee_bridge.services import orders
	start = helpers.ymd_to_epoch(year, month, 1)
	end = helpers.ymd_to_epoch(year + (1 if month == 12 else 0), (1 if month == 12 else month + 1), 1)
	order_sns = orders.get_order_list(start, end)
	# Filter <= end
	order_sns = [sn for sn in order_sns if sn]  # assume all are within
	if not order_sns:
		return {"count": 0, "first_order_sn": None, "last_order_sn": None, "min_created": None, "max_created": None}
	first_order_sn = order_sns[0]
	last_order_sn = order_sns[-1]
	# Get details for min max created
	created_times = []
	for sn in order_sns:
		detail = orders.get_order_details(sn)
		if detail and "create_time" in detail:
			created_times.append(detail["create_time"])
	min_created = min(created_times) if created_times else None
	max_created = max(created_times) if created_times else None
	return {"count": len(order_sns), "first_order_sn": first_order_sn, "last_order_sn": last_order_sn, "min_created": min_created, "max_created": max_created}


__all__ = [
	# Auth & Connection
	"connect_to_shopee",
	"oauth_callback", 
	"test_shopee_connection",
	"refresh_token",
	
	# Webhooks
	"webhook_live",
	"webhook_test",
	"get_webhook_logs",
	"retry_webhook",
	
	# Orders
	"get_order",
	"sync_order",
	"sync_orders_api",
	"update_order_status",
	
	# Logistics
	"get_shipping_info",
	"sync_shipping_api",
	"update_tracking",
	
	# Returns
	"get_returns",
	"sync_returns_api", 
	"process_return",
	
	# Finance
	"get_payout_info",
	"sync_finance_api",
	"reconcile_bank_api",
	"sync_escrow_batch",
	
	# Utilities
	"get_health_status",
	"audit_orders",
	"debug_webhook_payload",
	"sync_recent_orders",
	"check_token_health",
	"get_sync_logs",
	"debug_sign",
	"audit_shopee_orders_for_month",
]

