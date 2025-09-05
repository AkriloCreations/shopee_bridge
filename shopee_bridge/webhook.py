"""Shopee Webhook handlers and verifiers.

This module provides webhook signature verification, inbox management,
and push event processing for Shopee webhooks.
"""

from __future__ import annotations

from typing import Dict, Any, Optional
import hashlib
import hmac
import json
import time

import frappe

from shopee_bridge import auth, helpers
from shopee_bridge.services import webhook_handlers


class WebhookVerificationError(Exception):
	"""Raised when webhook signature verification fails."""
	pass


def verify_webhook_signature(raw_body: bytes, signature: str, push_key: str, timestamp: Optional[int] = None) -> bool:
	"""Verify Shopee webhook signature.
	
	Args:
		raw_body: Raw request body bytes
		signature: Signature from header
		push_key: Push key from settings
		timestamp: Optional timestamp for drift check
		
	Returns:
		True if signature is valid
		
	Raises:
		WebhookVerificationError: If verification fails
	"""
	try:
		# Create signature base string
		body_str = raw_body.decode('utf-8')
		base_string = f"{body_str}|"
		
		# Generate expected signature
		computed_signature = hmac.new(
			push_key.encode('utf-8'),
			base_string.encode('utf-8'),
			hashlib.sha256
		).hexdigest()
		
		# Use constant-time comparison
		if not hmac.compare_digest(signature, computed_signature):
			raise WebhookVerificationError("Invalid webhook signature")
		
		# Check timestamp drift if provided
		if timestamp:
			now = int(time.time())
			drift = abs(now - timestamp)
			if drift > 300:  # 5 minutes
				raise WebhookVerificationError(f"Timestamp drift too large: {drift}s")
		
		return True
		
	except UnicodeDecodeError:
		raise WebhookVerificationError("Unable to decode webhook body as UTF-8")
	except Exception as e:
		raise WebhookVerificationError(f"Webhook verification failed: {str(e)}")


def create_webhook_inbox(payload: Dict[str, Any], source_env: str, signature_valid: bool = False) -> str:
	"""Create a webhook inbox record.
	
	Args:
		payload: Webhook payload
		source_env: 'live' or 'test'
		signature_valid: Whether signature verification passed
		
	Returns:
		Name of created inbox record
		
	Raises:
		frappe.ValidationError: On validation errors
	"""
	
	# Derive idempotency key
	idempotency_key = derive_idempotency_key(payload)
	
	# Check for existing record
	existing = frappe.db.get_value("Shopee Webhook Inbox", {"idempotency_key": idempotency_key})
	if existing:
		return existing
	
	# Create payload hash
	payload_json = json.dumps(payload, sort_keys=True)
	payload_hash = helpers.create_payload_hash(payload)
	
	# Validate required fields
	if not payload.get("event_type"):
		frappe.throw("Missing event_type in webhook payload")
	
	# Create inbox record
	inbox = frappe.get_doc({
		"doctype": "Shopee Webhook Inbox",
		"event_type": payload.get("event_type") or payload.get("type") or "unknown",
		"source_env": source_env,
		"idempotency_key": idempotency_key,
		"signature_valid": signature_valid,
		"status": "queued",
		"payload_hash": payload_hash,
		"payload_json": payload_json,
		"attempts": 0,
	}).insert(ignore_permissions=True)
	
	frappe.db.commit()
	
	# Log creation
	frappe.logger().info(f"[Shopee] Webhook inbox created: {inbox.name} ({idempotency_key})")
	
	return inbox.name


def derive_idempotency_key(payload: Dict[str, Any]) -> str:
	"""Derive a stable idempotency key from webhook payload.
	
	Priority order:
	1. event_id if present
	2. Composite of event_type, entity identifiers, status, update_time
	"""
	if event_id := payload.get("event_id"):
		return str(event_id)
	
	# Build composite key
	parts = []
	
	# Event type
	event_type = payload.get("event_type") or payload.get("type") or "unknown"
	parts.append(event_type)
	
	# Entity identifiers
	for key in ["order_sn", "return_sn", "tracking_number", "package_number"]:
		if value := payload.get(key):
			parts.append(str(value))
	
	# Status
	if status := payload.get("status"):
		parts.append(str(status))
	
	# Update time
	if update_time := payload.get("update_time") or payload.get("updated_time"):
		parts.append(str(update_time))
	
	# Fallback to full payload hash if no identifying fields
	if len(parts) <= 1:
		payload_str = json.dumps(payload, sort_keys=True)
		return hashlib.sha1(payload_str.encode('utf-8')).hexdigest()
	
	# Create composite key
	composite = "|".join(parts)
	return hashlib.sha1(composite.encode('utf-8')).hexdigest()


def get_push_key(source_env: str) -> str:
	"""Get the appropriate push key for webhook verification.
	
	Args:
		source_env: 'live' or 'test'
		
	Returns:
		Push key string
		
	Raises:
		frappe.ValidationError: If key not configured
	"""
	settings = frappe.get_cached_doc("Shopee Settings")
	
	if source_env == "live":
		key = settings.get_password("live_partner_push_key")
		if not key:
			raise frappe.ValidationError("Live partner push key not configured")
	else:
		key = settings.get_password("test_partner_push_key")
		if not key:
			raise frappe.ValidationError("Test partner push key not configured")
	
	return key


def process_webhook_inbox(inbox_name: str) -> Dict[str, Any]:
	"""Process a webhook inbox entry.
	
	Args:
		inbox_name: Name of inbox record
		
	Returns:
		Processing result
	"""
	try:
		inbox = frappe.get_doc("Shopee Webhook Inbox", inbox_name)
		
		# Skip if already processed
		if inbox.status in ["done", "skipped"]:
			return {"status": "skipped", "message": f"Already {inbox.status}"}
		
		# Update status
		inbox.status = "processing"
		inbox.attempts = (inbox.attempts or 0) + 1
		inbox.save(ignore_permissions=True)
		
		# Parse payload
		payload = json.loads(inbox.payload_json)
		
		# Route to appropriate handler
		event_type = (payload.get("event_type") or "").lower()
		
		if event_type.startswith("order."):
			webhook_handlers.handle_order_push(payload, inbox.source_env)
		elif event_type.startswith("returns."):
			webhook_handlers.handle_return_push(payload, inbox.source_env)
		elif event_type.startswith("logistics."):
			webhook_handlers.handle_logistics_push(payload, inbox.source_env)
		else:
			inbox.status = "skipped"
			inbox.error_message = f"Unknown event type: {event_type}"
		
		# Mark as done
		inbox.status = "done"
		inbox.processed_at = frappe.utils.now_datetime()
		inbox.save(ignore_permissions=True)
		
		return {"status": "success", "event_type": event_type}
		
	except Exception as e:
		# Mark as failed
		try:
			inbox.status = "failed"
			inbox.error_message = str(e)[:500]
			inbox.save(ignore_permissions=True)
		except:
			pass
		
		frappe.log_error(f"Webhook processing failed: {e}", "Shopee Webhook")
		return {"status": "failed", "error": str(e)}


def retry_failed_webhooks(limit: int = 50) -> Dict[str, Any]:
	"""Retry failed webhook processing.
	
	Args:
		limit: Maximum number of retries
		
	Returns:
		Retry results
	"""
	failed_webhooks = frappe.get_list(
		"Shopee Webhook Inbox",
		filters={
			"status": "failed",
			"next_retry_at": ["<=", frappe.utils.now_datetime()]
		},
		fields=["name"],
		limit=limit,
		order_by="modified asc"
	)
	
	results = {"total": len(failed_webhooks), "successful": 0, "failed": 0}
	
	for webhook in failed_webhooks:
		try:
			result = process_webhook_inbox(webhook.name)
			if result.get("status") == "success":
				results["successful"] += 1
			else:
				results["failed"] += 1
		except Exception as e:
			results["failed"] += 1
			frappe.logger().error(f"Retry failed for {webhook.name}: {e}")
	
	return results


__all__ = [
	"verify_webhook_signature",
	"create_webhook_inbox", 
	"derive_idempotency_key",
	"get_push_key",
	"process_webhook_inbox",
	"retry_failed_webhooks",
	"WebhookVerificationError"
]