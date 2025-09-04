"""Webhook push event handlers (glue only).

Processes Shopee push payloads already validated & stored in the inbox. The
functions here are intentionally thin: they perform idempotency / anti-
regression checks (via `last_pushed_update_time` custom field if present), call
into service layer helpers (orders / returns / logistics), and log outcomes.

Rules:
 - Never perform outbound HTTP (webhook path must be fast and reliable).
 - Avoid raising for recoverable conditions (missing doc / outdated event).
 - Raise only on unrecoverable schema or programming errors.
 - Idempotency: skip if incoming update_time <= stored last_pushed_update_time.
 - All timestamps treated as seconds since epoch (int). Non‑parsable values -> 0.

NOTE: Many custom fields referenced may not yet exist in early deployments;
setters are wrapped in try/except to stay resilient.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
import frappe

from . import orders, returns, logistics


def _logger():  # central logger accessor
	return frappe.logger()


def _get_int(val: Any) -> int:
	try:
		if val is None:
			return 0
		return int(val)
	except Exception:
		return 0


def _get_last_pushed(doctype: str, filters: Dict[str, Any]) -> Optional[int]:
	try:
		return frappe.db.get_value(doctype, filters, "last_pushed_update_time")  # type: ignore
	except Exception:
		return None


def _maybe_set_last_pushed(doctype: str, name: str, ts: int):
	if not ts:
		return
	try:  # ignore schema issues
		frappe.db.set_value(doctype, name, "last_pushed_update_time", ts)
	except Exception:
		pass


def handle_order_push(event: Dict[str, Any], env: str) -> None:
	"""Process order push payload.

	Expected keys: order_sn, order_status, update_time.
	Performs anti‑regression check and invokes order upsert + document ensures.
	"""
	order_sn = event.get("order_sn")
	if not order_sn:
		_logger().warning(f"[Shopee][webhook][order] missing order_sn env={env}")
		return
	upd_ts = _get_int(event.get("update_time") or event.get("updated_time"))
	# Anti-regression: compare last_pushed_update_time on Sales Order
	last = _get_last_pushed("Sales Order", {"shopee_order_sn": order_sn})
	if last and upd_ts and upd_ts <= last:
		_logger().info(
			f"[Shopee][webhook][order] skip outdated order_sn={order_sn} incoming={upd_ts} stored={last}"
		)
		return
	try:
		so = orders.upsert_sales_order(event)
		status = (event.get("order_status") or "").lower()
		si = None
		dn = None
		if status in {"paid", "ready_to_ship", "completed"}:
			si = orders.ensure_sales_invoice_for_paid(so, event)
		if status in {"ready_to_ship", "completed"}:
			dn = orders.ensure_delivery_note_for_ready(si or so, event)
		if status == "completed":
			orders.on_completed(order_sn)
		_maybe_set_last_pushed("Sales Order", so, upd_ts)
		_logger().info(
			f"[Shopee][webhook][order] processed order_sn={order_sn} so={so} si={si} dn={dn} status={status}"
		)
	except Exception as exc:  # pragma: no cover - unexpected programming errors
		frappe.log_error(message=str(exc), title="Shopee Order Push Failure")
		raise


def handle_return_push(event: Dict[str, Any], env: str) -> None:
	"""Process return / refund push payload.

	Expected keys: return_sn, status, update_time.
	"""
	return_sn = event.get("return_sn") or event.get("returnsn")
	if not return_sn:
		_logger().warning(f"[Shopee][webhook][return] missing return_sn env={env}")
		return
	upd_ts = _get_int(event.get("update_time"))
	# (Optional) anti-regression could read Issue.last_pushed_update_time; skipped (unknown doctype)
	try:
		issue = returns.upsert_customer_issue_from_return(event)
		status = (event.get("status") or "").lower()
		if status in {"completed", "refunded", "closed"}:
			# Create sales return / credit note stub then close
			sr = returns.create_sales_return_or_credit_note(issue)
			returns.close_return_case(issue)
			_logger().info(
				f"[Shopee][webhook][return] finalized return_sn={return_sn} issue={issue} sr={sr} status={status}"
			)
		else:
			_logger().info(
				f"[Shopee][webhook][return] processed return_sn={return_sn} issue={issue} status={status}"
			)
	except Exception as exc:  # pragma: no cover
		frappe.log_error(message=str(exc), title="Shopee Return Push Failure")
		raise


def handle_logistics_push(event: Dict[str, Any], env: str) -> None:
	"""Process logistics / tracking push payload.

	Extracts order_sn or tracking_number and updates local tracking state via
	logistics.update_tracking_status (stub). Anti-regression logic can be
	introduced when Delivery Note custom fields are in place.
	"""
	order_sn = event.get("order_sn")
	tracking = event.get("tracking_number") or event.get("tracking_no")
	if not (order_sn or tracking):
		_logger().warning(f"[Shopee][webhook][logistics] missing keys env={env}")
		return
	upd_ts = _get_int(event.get("update_time"))
	# Derive a mock DN name pattern (real implementation would look up by order / tracking)
	dn_name = f"DN-{order_sn or tracking}"
	try:
		logistics.update_tracking_status(dn_name, event)
		_logger().info(
			f"[Shopee][webhook][logistics] updated dn={dn_name} tracking={tracking} order_sn={order_sn} ts={upd_ts}"
		)
	except Exception as exc:  # pragma: no cover
		frappe.log_error(message=str(exc), title="Shopee Logistics Push Failure")
		raise


__all__ = [
	"handle_order_push",
	"handle_return_push",
	"handle_logistics_push",
]

