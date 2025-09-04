"""Order service layer (Shopee -> ERPNext mapping).

This module encapsulates business logic for pulling order data from Shopee and
upserting ERPNext sales documents. External interaction (HTTP, signing) is
delegated to `clients` and `auth`. Public API kept stable for jobs / API layer.

Current implementation is intentionally lightweight / partially mocked:
 - Uses real signed GET calls for list & detail endpoints.
 - ERPNext create/update logic is represented by TODO blocks returning mock names
   so the sync pipeline can be exercised incrementally without side effects.
 - Idempotency keyed by custom field `shopee_order_sn` (expected to exist on
   Sales Order / Sales Invoice / Delivery Note via custom field creation).
 - Prevents status downgrade by comparing `last_pushed_update_time` custom field
   before applying transitions (mocked logic placeholder).

Functions return JSON-serializable types for easy logging and testing.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple
import time
import math
import frappe

from .. import clients

ORDER_LIST_PATH = "/api/v2/order/get_order_list"
ORDER_DETAIL_PATH = "/api/v2/order/get_order_detail"


def _log_sync(event: str, data: Dict[str, Any]):  # small centralized log helper
	try:
		frappe.logger().info(f"[Shopee][orders] {event} {data}")
	except Exception:  # pragma: no cover
		pass


def get_order_list(time_from: int, time_to: int, status: str | None, page_size: int = 100) -> List[str]:
	"""Fetch list of order_sn within time window.

	Args:
		time_from: Unix epoch (seconds) inclusive lower bound.
		time_to: Unix epoch (seconds) inclusive upper bound.
		status: Optional Shopee order status filter.
		page_size: Page size (Shopee max typically 100).
	Returns:
		List of order_sn strings.
	"""
	params: Dict[str, Any] = {
		"time_range_field": "update_time",
		"time_from": int(time_from),
		"time_to": int(time_to),
		"page_size": min(max(int(page_size), 1), 100),
		"order_status": status or "",  # Shopee may treat empty as all
	}
	order_sns: List[str] = []
	more = True
	cursor = None
	while more:
		if cursor:
			params["cursor"] = cursor
		resp = clients.http_get(ORDER_LIST_PATH, params)
		data = resp.get("response") or resp  # adapt to actual API structure once known
		# Expect data like { 'order_list': [ { 'order_sn': '...' }, ... ], 'more': True, 'next_cursor': '...' }
		for row in (data.get("order_list") or []):
			sn = row.get("order_sn")
			if sn:
				order_sns.append(sn)
		more = bool(data.get("more")) and bool(data.get("next_cursor"))
		cursor = data.get("next_cursor")
		if not more:
			break
	return order_sns


def get_order_detail(order_sn_list: List[str]) -> List[Dict[str, Any]]:
	"""Fetch detailed order objects.

	Batches list into chunks to respect API size limits (assume <= 50 per call).
	"""
	results: List[Dict[str, Any]] = []
	if not order_sn_list:
		return results
	chunk_size = 50
	for i in range(0, len(order_sn_list), chunk_size):
		chunk = order_sn_list[i : i + chunk_size]
		params = {"order_sn_list": ",".join(chunk)}
		resp = clients.http_get(ORDER_DETAIL_PATH, params)
		data = resp.get("response") or resp
		for od in (data.get("order_list") or data.get("orders") or []):
			results.append(od)
	return results


def ensure_customer_and_addresses(order: Dict[str, Any]) -> Tuple[str, str]:
	"""Ensure ERPNext Customer & Address for order.

	Priority for customer naming:
		1. buyer_username (explicit request)
		2. buyer_user_id
		3. order_sn fallback => UNKNOWN

	For now still mocked: returns synthesized names only.
	"""
	raw_username = (order.get("buyer_username") or "").strip()
	raw_user_id = (order.get("buyer_user_id") or "").strip()
	base = raw_username or raw_user_id or "UNKNOWN"
	# Basic sanitation: limit length & remove spaces that could cause naming collisions later
	safe_base = base.replace(" ", "_")[:140] or "UNKNOWN"
	customer_name = f"SHP-{safe_base}"
	address_name = f"ADDR-{order.get('order_sn')}"
	return customer_name, address_name


def upsert_sales_order(order: Dict[str, Any]) -> str:
	"""Create or update Sales Order for Shopee order.

	Idempotency via custom field `shopee_order_sn`.
	Returns Sales Order name (mocked if not implemented).
	"""
	order_sn = order.get("order_sn") or "UNKNOWN"
	# TODO: search Sales Order by shopee_order_sn and update / create accordingly.
	so_name = f"SO-{order_sn}"
	return so_name


def ensure_sales_invoice_for_paid(so_name: str, order: Dict[str, Any]) -> str:
	"""If order is paid, ensure a Sales Invoice exists (mocked)."""
	# TODO: implement state check & invoice creation.
	return f"SI-{so_name}"


def ensure_delivery_note_for_ready(so_or_si: str, order: Dict[str, Any]) -> str:
	"""If order status indicates ready to ship, ensure Delivery Note exists."""
	# TODO: implement shipping readiness logic.
	return f"DN-{so_or_si}"  # use base name for determinism


def on_completed(order_sn: str) -> None:
	"""Hook invoked when order reaches completed state (placeholder)."""
	# TODO: perform finalization tasks (e.g., mark as fulfilled, trigger notifications)
	_log_sync("completed", {"order_sn": order_sn})


def sync_incremental_orders(updated_since_minutes: int = 15) -> Dict[str, Any]:
	"""High-level incremental sync pipeline.

	Steps:
		1. Calculate time window (now - minutes, now).
		2. Pull order_sn list.
		3. Pull detailed orders.
		4. Upsert ERPNext docs per order.
		5. Aggregate results & per-order errors.

	Returns summary dict.
	"""
	started = int(time.time())
	window_to = started
	window_from = window_to - (updated_since_minutes * 60)
	summary = {
		"window_from": window_from,
		"window_to": window_to,
		"minutes": updated_since_minutes,
		"orders_found": 0,
		"orders_processed": 0,
		"errors": [],
		"duration_s": 0,
	}
	try:
		order_sn_list = get_order_list(window_from, window_to, status=None)
		summary["orders_found"] = len(order_sn_list)
		details = get_order_detail(order_sn_list)
		for od in details:
			try:
				so = upsert_sales_order(od)
				status = (od.get("order_status") or "").lower()
				if status in {"paid", "ready_to_ship", "completed"}:
					si = ensure_sales_invoice_for_paid(so, od)
				else:
					si = None
				if status in {"ready_to_ship", "completed"}:
					dn = ensure_delivery_note_for_ready(si or so, od)  # prefer invoice if created
				else:
					dn = None
				if status == "completed":
					on_completed(od.get("order_sn"))
				summary["orders_processed"] += 1
				_log_sync("order_processed", {"order_sn": od.get("order_sn"), "so": so, "si": si, "dn": dn})
			except Exception as per_exc:  # record per-order error, continue
				err_msg = f"{od.get('order_sn')}: {per_exc}"[:500]
				summary["errors"].append(err_msg)
				frappe.log_error(message=err_msg, title="Shopee Order Sync Error")
	except Exception as exc:
		main_err = str(exc)
		summary["errors"].append(main_err)
		frappe.log_error(message=main_err, title="Shopee Order Sync Fatal")
	finally:
		summary["duration_s"] = round(time.time() - started, 2)
	return summary


__all__ = [
	"get_order_list",
	"get_order_detail",
	"ensure_customer_and_addresses",
	"upsert_sales_order",
	"ensure_sales_invoice_for_paid",
	"ensure_delivery_note_for_ready",
	"on_completed",
	"sync_incremental_orders",
]

