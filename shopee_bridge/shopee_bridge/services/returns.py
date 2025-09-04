"""Returns / refunds service layer.

Wraps Shopee returns endpoints providing higher‑level helpers for ERPNext.
All ERP document mutations are deferred (TODO placeholders) so that early
integration tests can run safely without side effects.

Idempotency:
 - Intended ERP Issue (or custom DocType) keyed by `return_sn` stored in a
   custom field (e.g., shopee_return_sn).
 - Anti‑regression: compare incoming `update_time` to stored
   `last_return_update_time` (not yet implemented – TODO).
"""

from __future__ import annotations

from typing import Any, Dict, List
import time
import frappe

from .. import clients

RETURN_LIST_PATH = "/api/v2/returns/get_return_list"
RETURN_DETAIL_PATH = "/api/v2/returns/get_return_detail"
AVAILABLE_SOLUTION_PATH = "/api/v2/returns/get_available_solution"
OFFER_SOLUTION_PATH = "/api/v2/returns/offer"
ACCEPT_OFFER_PATH = "/api/v2/returns/accept_offer"
DISPUTE_PATH = "/api/v2/returns/dispute"
UPLOAD_PROOF_PATH = "/api/v2/returns/upload_proof"
CONFIRM_RETURN_PATH = "/api/v2/returns/confirm"


def _log(event: str, data: Dict[str, Any]):
	try:
		frappe.logger().info(f"[Shopee][returns] {event} {data}")
	except Exception:  # pragma: no cover
		pass


def get_return_list(time_from: int, time_to: int, status: str | None) -> List[str]:
	"""Return list of return_sn within window."""
	params: Dict[str, Any] = {
		"time_range_field": "update_time",
		"time_from": int(time_from),
		"time_to": int(time_to),
	}
	if status:
		params["status"] = status
	ret_sns: List[str] = []
	more = True
	cursor = None
	while more:
		if cursor:
			params["cursor"] = cursor
		resp = clients.http_get(RETURN_LIST_PATH, params)
		data = resp.get("response") or resp
		for row in (data.get("returns") or data.get("return_list") or []):
			sn = row.get("return_sn") or row.get("returnsn")
			if sn:
				ret_sns.append(sn)
		more = bool(data.get("more")) and bool(data.get("next_cursor"))
		cursor = data.get("next_cursor")
		if not more:
			break
	return ret_sns


def get_return_detail(return_sn: str) -> Dict[str, Any]:
	"""Fetch detailed return object."""
	resp = clients.http_get(RETURN_DETAIL_PATH, {"return_sn": return_sn})
	return resp.get("response") or resp


def get_available_solution(return_sn: str) -> List[Dict[str, Any]]:
	"""Fetch available resolution options."""
	resp = clients.http_get(AVAILABLE_SOLUTION_PATH, {"return_sn": return_sn})
	data = resp.get("response") or resp
	return data.get("solutions") or data.get("available_solutions") or []


def offer_solution(return_sn: str, solution: Dict[str, Any]) -> Dict[str, Any]:
	"""Offer a solution to buyer (stub)."""
	payload = {"return_sn": return_sn, **(solution or {})}
	resp = clients.http_post(OFFER_SOLUTION_PATH, json=payload)
	return resp.get("response") or resp


def accept_offer(return_sn: str) -> Dict[str, Any]:
	"""Accept buyer's / platform's offer (stub)."""
	resp = clients.http_post(ACCEPT_OFFER_PATH, json={"return_sn": return_sn})
	return resp.get("response") or resp


def raise_dispute(return_sn: str, reason: str) -> Dict[str, Any]:
	"""Raise dispute (stub)."""
	resp = clients.http_post(DISPUTE_PATH, json={"return_sn": return_sn, "reason": reason})
	return resp.get("response") or resp


def upload_proof(return_sn: str, files: List[bytes]) -> Dict[str, Any]:
	"""Upload proof photos / docs.

	NOTE: Actual multipart upload not yet implemented – placeholder only.
	"""
	# TODO: implement multipart when needed. For now treat file lengths as metadata.
	meta = [{"size": len(b)} for b in (files or [])]
	resp = clients.http_post(UPLOAD_PROOF_PATH, json={"return_sn": return_sn, "files_meta": meta})
	return resp.get("response") or resp


def confirm_return(return_sn: str) -> Dict[str, Any]:
	"""Confirm successful return (stub)."""
	resp = clients.http_post(CONFIRM_RETURN_PATH, json={"return_sn": return_sn})
	return resp.get("response") or resp


def upsert_customer_issue_from_return(payload: Dict[str, Any]) -> str:
	"""Create or update ERP Issue / Return doc (mock).

	Returns mock issue name for now.
	"""
	return_sn = payload.get("return_sn") or "UNKNOWN"
	# TODO: implement real upsert by shopee_return_sn custom field.
	return f"ISS-{return_sn}"


def create_sales_return_or_credit_note(issue_name: str) -> str:
	"""Create Sales Return / Credit Note for resolved return (mock)."""
	# TODO: implement mapping using issue links (SO/SI) when available.
	return f"SR-{issue_name}"


def close_return_case(issue_name: str) -> None:
	"""Mark Issue / Return record as closed (stub)."""
	# TODO: update doc status, add timeline comment.
	_log("close_case", {"issue": issue_name})


def sync_returns_incremental(updated_since_minutes: int = 30) -> Dict[str, Any]:
	"""Incremental sync pipeline for returns (stub).

	Steps:
		1. Determine time window.
		2. Pull list of return_sn.
		3. Fetch detail & upsert Issue (mock) per return.
		4. Summarize results.
	"""
	now = int(time.time())
	window_from = now - updated_since_minutes * 60
	summary = {
		"window_from": window_from,
		"window_to": now,
		"minutes": updated_since_minutes,
		"returns_found": 0,
		"returns_processed": 0,
		"errors": [],
	}
	try:
		ret_list = get_return_list(window_from, now, status=None)
		summary["returns_found"] = len(ret_list)
		for rsn in ret_list:
			try:
				detail = get_return_detail(rsn)
				issue = upsert_customer_issue_from_return(detail)
				summary["returns_processed"] += 1
				_log("return_processed", {"return_sn": rsn, "issue": issue})
			except Exception as per_exc:  # pragma: no cover
				err = f"{rsn}: {per_exc}"[:400]
				summary["errors"].append(err)
				frappe.log_error(message=err, title="Shopee Return Sync Error")
	except Exception as exc:
		summary["errors"].append(str(exc))
		frappe.log_error(message=str(exc), title="Shopee Return Sync Fatal")
	return summary


__all__ = [
	"get_return_list",
	"get_return_detail",
	"get_available_solution",
	"offer_solution",
	"accept_offer",
	"raise_dispute",
	"upload_proof",
	"confirm_return",
	"upsert_customer_issue_from_return",
	"create_sales_return_or_credit_note",
	"close_return_case",
	"sync_returns_incremental",
]

