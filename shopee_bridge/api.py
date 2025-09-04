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

from . import auth


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
		from .services import orders
		order_data = orders.get_order_details(order_sn)
		return _result({"order": order_data})
	except Exception as e:
		return _error(e)


@frappe.whitelist()
def sync_order(order_sn: str) -> Dict[str, Any]:
	"""Sync specific order from Shopee to ERPNext."""
	try:
		from .services import orders
		result = orders.sync_single_order(order_sn)
		return _result({"sync_result": result})
	except Exception as e:
		return _error(e)


@frappe.whitelist()
def update_order_status(order_sn: str, status: str) -> Dict[str, Any]:
	"""Update order status in Shopee."""
	try:
		from .services import orders
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
		result = auth.refresh_access_token()
		return _result({"token_refreshed": result})
	except Exception as e:
		return _error(e)


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
]

