"""Shopee Webhook Inbox dispatcher job.

Processes queued inbox entries, routes to appropriate service handler, and
manages retry scheduling with exponential-ish backoff.
"""

from __future__ import annotations

from typing import Dict, Any, List
import hashlib
import json
import time
import frappe

from ..services import webhook_handlers

BACKOFF_SCHEDULE_SECONDS = [60, 300, 900, 3600, 10800]  # 1m,5m,15m,1h,3h


def derive_idempotency_key(event: Dict[str, Any]) -> str:
	"""Derive a stable idempotency key for a webhook event payload.

	Priority: event_id field if present else SHA1 composite of
	event_type, entity (order_sn/return_sn/tracking_number), status, update_time.
	"""
	if not isinstance(event, dict):  # defensive
		return "invalid-event"
	if event_id := event.get("event_id"):
		return str(event_id)
	event_type = event.get("event_type") or event.get("type") or "unknown"
	entity = (
		event.get("order_sn")
		or event.get("return_sn")
		or event.get("returnsn")
		or event.get("tracking_number")
		or event.get("tracking_no")
		or "entity"
	)
	status = event.get("status") or event.get("order_status") or "status"
	update_time = event.get("update_time") or event.get("updated_time") or 0
	raw = f"{event_type}:{entity}:{status}:{update_time}"
	return hashlib.sha1(raw.encode("utf-8")).hexdigest()  # noqa: S324 (idempotency only)


def _log(msg: str):
	frappe.logger().info(f"[Shopee][webhook][job] {msg}")


def _short_err(e: Exception) -> str:
	return str(e)[:500]


def run(inbox: str) -> None:  # pragma: no cover - scheduled/async context
	"""Process a single Shopee Webhook Inbox entry by name.

	Steps:
	  1. Load inbox doc; exit early if already terminal.
	  2. Set status=processing, attempts +=1.
	  3. Parse payload JSON.
	  4. Route based on event_type prefix.
	  5. Success -> status=done; Failure -> status=failed with backoff schedule.
	"""
	try:
		doc = frappe.get_doc("Shopee Webhook Inbox", inbox)
	except Exception as exc:
		_log(f"missing inbox={inbox} err={exc}")
		return
	if doc.status in {"done", "skipped"}:
		_log(f"skip terminal inbox={inbox} status={doc.status}")
		return
	# optimistic locking pattern minimal (no explicit row lock here)
	doc.status = "processing"
	doc.attempts = (doc.attempts or 0) + 1
	doc.error_message = None
	try:
		payload = json.loads(doc.payload_json or "{}")
	except Exception:
		payload = {}
	event_type = (payload.get("event_type") or payload.get("type") or "").lower()
	env = doc.source_env or "live"
	try:
		if event_type.startswith("order."):
			webhook_handlers.handle_order_push(payload, env)
		elif event_type.startswith("returns."):
			webhook_handlers.handle_return_push(payload, env)
		elif event_type.startswith("logistics."):
			webhook_handlers.handle_logistics_push(payload, env)
		else:
			doc.status = "skipped"
			doc.error_message = f"unknown event_type={event_type}"[:140]
			doc.processed_at = frappe.utils.now_datetime()
			doc.save(ignore_permissions=True)
			_log(f"skipped inbox={inbox} event_type={event_type}")
			return
		# success path
		doc.status = "done"
		doc.processed_at = frappe.utils.now_datetime()
		doc.save(ignore_permissions=True)
		_log(f"done inbox={inbox} attempts={doc.attempts} event_type={event_type}")
	except Exception as exc:  # handler failure
		delay = BACKOFF_SCHEDULE_SECONDS[min(doc.attempts - 1, len(BACKOFF_SCHEDULE_SECONDS) - 1)]
		next_retry = frappe.utils.add_to_date(frappe.utils.now_datetime(), seconds=delay)
		doc.status = "failed"
		doc.error_message = _short_err(exc)
		doc.next_retry_at = next_retry
		doc.save(ignore_permissions=True)
		frappe.log_error(message=_short_err(exc), title="Shopee Webhook Handler Error")
		_log(f"failed inbox={inbox} attempt={doc.attempts} delay={delay}s err={exc}")


def retry_due() -> Dict[str, Any]:
	"""Enqueue retry jobs for failed inbox entries whose next_retry_at is due.

	Returns dict with counts of enqueued and remaining failures.
	"""
	now = frappe.utils.now_datetime()
	due = frappe.get_all(
		"Shopee Webhook Inbox",
		filters={
			"status": "failed",
			"next_retry_at": ("<=", now),  # proper filter tuple
		},
		fields=["name"],
		limit=100,
		order_by="modified asc",
	)
	enqueued = 0
	for row in due:
		frappe.enqueue(
			"shopee_bridge.jobs.process_webhook.run",
			inbox=row["name"],
			queue="short",
			enqueue_after_commit=True,
		)
		enqueued += 1
	remaining = frappe.db.count("Shopee Webhook Inbox", {"status": "failed"})
	return {"enqueued": enqueued, "remaining_failed": remaining}


__all__ = ["derive_idempotency_key", "run", "retry_due"]

