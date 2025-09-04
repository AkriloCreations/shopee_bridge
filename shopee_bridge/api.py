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
	"""Handle OAuth redirect callback.

	All params passed through **params.
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
		auth.verify_webhook_signature(
			path=req.path, raw_body=raw_body, headers=headers, push_key=push_key
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


__all__ = [
	"connect_to_shopee",
	"oauth_callback",
	"test_shopee_connection",
	"webhook_live",
	"webhook_test",
	"sync_orders_api",
	"sync_finance_api",
]

