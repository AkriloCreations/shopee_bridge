"""Fiscal year & historical backfill utilities.

Provides orchestrator functions to run *chunked* backfills over long date
ranges by delegating to lower level service modules (orders, returns,
logistics, finance). All heavy business logic remains in those modules; this
file focuses on safe iteration, summarization, and integrity reporting.

Timezone: Target business timezone is Asia/Jakarta. For now we treat input
ISO dates (YYYY-MM-DD) as local dates in that timezone without explicit
conversion. TODO: Add proper tz handling using pytz/zoneinfo and convert to
UTC epochs for precise window alignment.
"""

from __future__ import annotations

from typing import Dict, List, Tuple, Iterable, Any
import datetime as _dt
import math
import frappe

from shopee_bridge.services import orders, returns, logistics, finance


def _iter_chunks(start: str, end: str, chunk_days: int) -> Iterable[Tuple[_dt.date, _dt.date]]:
	"""Yield inclusive date chunk tuples between start and end.

	Args:
		start: ISO date (YYYY-MM-DD) inclusive.
		end: ISO date inclusive.
		chunk_days: Maximum days per chunk (>=1).
	"""
	sd = _dt.date.fromisoformat(start)
	ed = _dt.date.fromisoformat(end)
	if ed < sd:
		raise ValueError("end before start")
	step = max(1, int(chunk_days))
	cur = sd
	while cur <= ed:
		chunk_end = min(ed, cur + _dt.timedelta(days=step - 1))
		yield cur, chunk_end
		cur = chunk_end + _dt.timedelta(days=1)


def _chunk_summary_header(start: _dt.date, end: _dt.date) -> Dict[str, Any]:
	return {"chunk_start": start.isoformat(), "chunk_end": end.isoformat()}


def backfill_orders_for_range(start: str, end: str, chunk_days: int = 7) -> Dict[str, Any]:
	"""Backfill orders across date range (stub orchestration).

	Current approach: For each chunk, call incremental sync using a window size
	approximated by the chunk length (days * 24 * 60 minutes). This leverages
	existing stub logic; later we can implement explicit date-bounded calls.
	"""
	results: List[Dict[str, Any]] = []
	errors: List[str] = []
	for cs, ce in _iter_chunks(start, end, chunk_days):
		minutes = int(((ce - cs).days + 1) * 24 * 60)
		try:
			res = orders.sync_incremental_orders(updated_since_minutes=minutes)
			results.append({**_chunk_summary_header(cs, ce), **res})
		except Exception as exc:  # pragma: no cover
			err = f"orders {cs}->{ce}: {exc}"[:400]
			errors.append(err)
			frappe.log_error(message=err, title="Shopee Orders Backfill Error")
	return {"range_start": start, "range_end": end, "chunks": results, "errors": errors}


def backfill_returns_for_range(start: str, end: str, chunk_days: int = 7) -> Dict[str, Any]:
	"""Backfill returns across range using incremental returns stub."""
	results: List[Dict[str, Any]] = []
	errors: List[str] = []
	for cs, ce in _iter_chunks(start, end, chunk_days):
		minutes = int(((ce - cs).days + 1) * 24 * 60)
		try:
			res = returns.sync_returns_incremental(updated_since_minutes=minutes)
			results.append({**_chunk_summary_header(cs, ce), **res})
		except Exception as exc:  # pragma: no cover
			err = f"returns {cs}->{ce}: {exc}"[:400]
			errors.append(err)
			frappe.log_error(message=err, title="Shopee Returns Backfill Error")
	return {"range_start": start, "range_end": end, "chunks": results, "errors": errors}


def backfill_shipping_for_range(start: str, end: str, chunk_days: int = 7) -> Dict[str, Any]:
	"""Backfill shipping status across range (stub)."""
	results: List[Dict[str, Any]] = []
	errors: List[str] = []
	for cs, ce in _iter_chunks(start, end, chunk_days):
		minutes = int(((ce - cs).days + 1) * 24 * 60)
		try:
			res = logistics.sync_shipping_status(updated_since_minutes=minutes)
			results.append({**_chunk_summary_header(cs, ce), **res})
		except Exception as exc:
			err = f"shipping {cs}->{ce}: {exc}"[:400]
			errors.append(err)
			frappe.log_error(message=err, title="Shopee Shipping Backfill Error")
	return {"range_start": start, "range_end": end, "chunks": results, "errors": errors}


def backfill_finance_for_range(start: str, end: str, min_age_hours: int = 3, chunk_days: int = 7) -> Dict[str, Any]:
	"""Backfill escrow / finance across range (stub orchestration).

	Uses finance.finance_backfill_range per chunk; min_age_hours currently
	unused in that stub but retained for future filtering.
	"""
	results: List[Dict[str, Any]] = []
	errors: List[str] = []
	for cs, ce in _iter_chunks(start, end, chunk_days):
		try:
			res = finance.finance_backfill_range(cs.isoformat(), ce.isoformat())
			res.update(_chunk_summary_header(cs, ce))
			results.append(res)
		except Exception as exc:
			err = f"finance {cs}->{ce}: {exc}"[:400]
			errors.append(err)
			frappe.log_error(message=err, title="Shopee Finance Backfill Error")
	return {
		"range_start": start,
		"range_end": end,
		"chunks": results,
		"errors": errors,
		"min_age_hours": min_age_hours,
	}


def reconcile_bank_for_range(start: str, end: str) -> Dict[str, Any]:
	"""Run strict reconciliation over a window (stub).

	Currently invokes a single strict reconcile ignoring the date range; future
	implementation will gather bank transactions constrained by date span.
	"""
	try:
		res = finance.reconcile_bank_strict(days_back=(
			( _dt.date.fromisoformat(end) - _dt.date.fromisoformat(start) ).days + 1
		))
		res.update({"range_start": start, "range_end": end})
		return res
	except Exception as exc:  # pragma: no cover
		frappe.log_error(message=str(exc), title="Shopee Reconcile Range Error")
		return {"range_start": start, "range_end": end, "error": str(exc)}


def generate_integrity_report(start: str, end: str) -> str:
	"""Generate integrity report over date range; returns File URL or name (mock).

	Planned contents:
	  - Counts: orders, invoices, returns, bank transactions.
	  - Discrepancies: orders without invoices, invoices without escrow, returns without credit notes.
	  - Hash digest of key metrics for tamper evidence.

	Current stub writes a small text file document.
	"""
	content = [
		"Shopee Bridge Integrity Report",
		f"Range: {start} -> {end}",
		"(Stub content – implement real metrics)",
	]
	data = "\n".join(content).encode("utf-8")
	file_doc = frappe.get_doc({
		"doctype": "File",
		"file_name": f"shopee_integrity_{start}_{end}.txt",
		"content": data,
		"is_private": 1,
	}).insert(ignore_permissions=True)
	return file_doc.file_url or file_doc.name


def run_fiscal_year_full_sync(company: str, fiscal_year_name: str) -> Dict[str, Any]:
	"""Orchestrate an end-to-end fiscal year full sync (stub).

	Strategy (future): derive fiscal year start/end dates from ERPNext Fiscal
	Year document, then sequentially invoke backfill for orders, returns,
	shipping, finance, reconciliation, and generate final integrity report.

	Current stub: attempts to load Fiscal Year for date window; if unavailable
	returns error. Then runs each backfill with default chunk size and compiles
	summary without performing real mutations.
	"""
	try:
		fy = frappe.get_doc("Fiscal Year", fiscal_year_name)
		start = fy.year_start_date.isoformat()
		end = fy.year_end_date.isoformat()
	except Exception as exc:
		return {"ok": False, "error": f"Fiscal Year lookup failed: {exc}"}

	orders_summary = backfill_orders_for_range(start, end)
	returns_summary = backfill_returns_for_range(start, end)
	shipping_summary = backfill_shipping_for_range(start, end)
	finance_summary = backfill_finance_for_range(start, end)
	reconcile_summary = reconcile_bank_for_range(start, end)
	report_url = generate_integrity_report(start, end)

	return {
		"ok": True,
		"company": company,
		"fiscal_year": fiscal_year_name,
		"orders": orders_summary,
		"returns": returns_summary,
		"shipping": shipping_summary,
		"finance": finance_summary,
		"reconcile": reconcile_summary,
		"report": report_url,
	}


__all__ = [
	"run_fiscal_year_full_sync",
	"backfill_orders_for_range",
	"backfill_returns_for_range",
	"backfill_shipping_for_range",
	"backfill_finance_for_range",
	"reconcile_bank_for_range",
	"generate_integrity_report",
]

