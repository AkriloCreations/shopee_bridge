"""Utility helpers for Shopee Bridge.

This module contains pure utility functions with no side effects.
All functions are designed to be import-safe and idempotent.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import time
import hashlib
import json


def epoch_now() -> int:
	"""Get current timestamp as epoch seconds (int)."""
	return int(time.time())


def now_epoch() -> int:
	"""Get current timestamp as epoch seconds (int)."""
	return int(time.time())


def safe(obj, maxlen: int = 256) -> str:
	"""Safely convert object to JSON-compact string, truncate to maxlen, never throw."""
	try:
		json_str = json.dumps(obj, separators=(",", ":"), sort_keys=True)
		return json_str[:maxlen]
	except Exception:
		return str(obj)[:maxlen]


def chunks(seq, size: int):
	"""Simple chunker for batching orders/escrow calls."""
	if not seq:
		return []
	result = []
	for i in range(0, len(seq), size):
		result.append(seq[i:i + size])
	return result


def ymd_to_epoch(y: int, m: int, d: int) -> int:
	"""Convert year/month/day to epoch seconds using calendar.timegm."""
	import calendar
	return calendar.timegm(time.struct_time((y, m, d, 0, 0, 0, -1, -1, 0)))


def safe_int(value: Any, default: int = 0) -> int:
	"""Safely convert value to int with fallback."""
	try:
		return int(value)
	except (ValueError, TypeError):
		return default


def safe_float(value: Any, default: float = 0.0) -> float:
	"""Safely convert value to float with fallback."""
	try:
		return float(value)
	except (ValueError, TypeError):
		return default


def safe_str(value: Any, default: str = "") -> str:
	"""Safely convert value to string with fallback."""
	if value is None:
		return default
	return str(value)


def truncate_text(text: str, max_length: int = 140) -> str:
	"""Truncate text to max length."""
	if not text:
		return ""
	return text[:max_length]


def generate_idempotency_key(*parts: Any) -> str:
	"""Generate stable idempotency key from parts."""
	clean_parts = [safe_str(part).strip() for part in parts if part]
	if not clean_parts:
		return ""
	
	combined = "|".join(clean_parts)
	return hashlib.sha1(combined.encode("utf-8")).hexdigest()


def deep_get(data: Dict[str, Any], *keys: str, default: Any = None) -> Any:
	"""Get nested dict value safely."""
	current = data
	for key in keys:
		if isinstance(current, dict) and key in current:
			current = current[key]
		else:
			return default
	return current


def normalize_order_status(status: str) -> str:
	"""Normalize order status to lowercase."""
	if not status:
		return ""
	return status.lower().strip()


def is_valid_epoch(ts: Any) -> bool:
	"""Check if timestamp is valid epoch seconds."""
	try:
		ts_int = int(ts)
		now = epoch_now()
		# Reasonable range: 1 year ago to 1 year from now
		return (now - 31536000) <= ts_int <= (now + 31536000)
	except (ValueError, TypeError):
		return False


def calculate_time_diff(start_ts: int, end_ts: int) -> int:
	"""Calculate time difference in seconds."""
	if not (is_valid_epoch(start_ts) and is_valid_epoch(end_ts)):
		return 0
	return abs(end_ts - start_ts)


def format_currency(amount: float, currency: str = "IDR") -> str:
	"""Format amount as currency string."""
	try:
		return f"{currency} {amount:,.2f}"
	except (ValueError, TypeError):
		return f"{currency} 0.00"


def validate_required_fields(data: Dict[str, Any], required: List[str]) -> List[str]:
	"""Validate required fields and return missing ones."""
	missing = []
	for field in required:
		if not deep_get(data, field):
			missing.append(field)
	return missing


def create_payload_hash(payload: Dict[str, Any]) -> str:
	"""Create hash of payload for deduplication."""
	try:
		# Sort keys for consistent hashing
		normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
		return hashlib.sha1(normalized.encode("utf-8")).hexdigest()
	except Exception:
		return ""


def batch_items(items: List[Any], batch_size: int) -> List[List[Any]]:
	"""Split items into batches."""
	if not items:
		return []
	
	batches = []
	for i in range(0, len(items), batch_size):
		batches.append(items[i:i + batch_size])
	
	return batches


def merge_dicts(*dicts: Dict[str, Any]) -> Dict[str, Any]:
	"""Merge multiple dictionaries."""
	result = {}
	for d in dicts:
		if isinstance(d, dict):
			result.update(d)
	return result


__all__ = [
	"epoch_now",
	"now_epoch",
	"safe_int", 
	"safe_float",
	"safe_str",
	"safe",
	"truncate_text",
	"generate_idempotency_key",
	"deep_get",
	"normalize_order_status",
	"is_valid_epoch",
	"calculate_time_diff",
	"format_currency",
	"validate_required_fields",
	"create_payload_hash",
	"batch_items",
	"chunks",
	"merge_dicts",
	"ymd_to_epoch"
]
