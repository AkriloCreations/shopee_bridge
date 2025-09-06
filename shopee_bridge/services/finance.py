"""Finance / escrow / reconciliation service layer.

Responsibilities (future full implementation):
 - Pull escrow detail for completed Shopee orders.
 - Patch related Sales Invoice with Shopee fee breakout.
 - Create Bank Transaction (or Journal Entry) representing Shopee payout.
 - Reconcile payouts strictly and perform historical backfills.

Current state: SAFE STUBS (no DB mutations) returning deterministic mock
identifiers so upstream jobs can be wired without side effects.

Domain notes / design contracts:
 1. Sales Invoice fee line: one negative row with item/name "Total Fee Shopee".
	If exists, update amounts instead of adding duplicates (idempotent).
 2. Custom fields expected on Sales Invoice (not yet created here):
	   - escrow_synced (Check)
	   - escrow_synced_at (Datetime)
	   - escrow_fee_total (Currency)
	   - escrow_net (Currency)
	   - payout_batch_id (Data)
	These allow detecting previous sync & preventing regressions.
 3. Idempotency heuristics:
	   - Fee patch keyed by (order_sn, payout_batch_id).
	   - Bank transaction creation keyed by (reference_number, deposit_amount).
 4. Reconciliation rule (strict): match bank transaction whose amount == escrow net
	   AND value date within tolerance (same day). If amount matches but date differs,
	   policy: adjust date or flag for manual review (TODO – decision pending).
 5. Backfill strategy: iterate orders (by completion date) in date range, pulling
	   escrow details and applying the same patch/create routines.

Endpoints used:
 - /api/v2/payment/get_escrow_detail (only external call in this stub set)

All other functions are placeholders with docstrings describing planned behavior.
"""

from __future__ import annotations

from typing import Any, Dict, List
import time

from shopee_bridge import clients
from shopee_bridge import helpers

ESCROW_DETAIL_PATH = "/api/v2/payment/get_escrow_detail"


def _log(event: str, data: Dict[str, Any]):  # light logging
	try:
		import frappe
		frappe.logger().info(f"[Shopee][finance] {event} {data}")
	except Exception:  # pragma: no cover
		pass


# Path=/api/v2/payment/get_escrow_detail, Method=GET,
# Query: order_sn, shop_id (+ partner_id, timestamp, sign, access_token auto by client),
# Body: none.
def get_escrow_detail(host: str, access_token: str, shop_id: int, order_sn: str) -> dict:
	"""Fetch escrow detail for a single order."""
	from shopee_bridge import clients, auth
	token = auth.get_valid_access_token()
	return clients.request_json(method="GET", host=host, path="/api/v2/payment/get_escrow_detail",
								query={"order_sn": order_sn, "shop_id": shop_id},
								body=None, access_token=token, shop_id=shop_id)


def patch_invoice_with_fees(escrow: Dict[str, Any]) -> str:
	"""Idempotently patch Sales Invoice with Shopee fee / net values."""
	import frappe
	order_sn = escrow.get("order_sn") or "UNKNOWN"
	payout_batch_id = escrow.get("payout_batch_id") or "BATCH-MOCK"
	try:
		si = frappe.get_doc({"doctype": "Sales Invoice", "shopee_order_sn": order_sn})
		# Check if already patched for this payout_batch_id
		if getattr(si, "payout_batch_id", None) == payout_batch_id:
			_log("invoice_already_patched", {"si": si.name, "batch": payout_batch_id})
			return si.name
		# Calculate fee_total and net (mock logic, replace with real fields)
		fee_total = float(escrow.get("fee_total", 0))
		gross = float(escrow.get("gross_amount", 0))
		net = gross - fee_total
		# Update or insert fee line (mock, real implementation should update child table)
		si.escrow_fee_total = fee_total
		si.escrow_net = net
		si.payout_batch_id = payout_batch_id
		si.escrow_synced = 1
		si.escrow_synced_at = frappe.utils.now_datetime()
		si.save(ignore_permissions=True)
		_log("invoice_patched", {"si": si.name, "batch": payout_batch_id, "fee": fee_total, "net": net})
		return si.name
	except frappe.DoesNotExistError:
		_log("invoice_not_found", {"order_sn": order_sn})
		return f"SI-{order_sn}"
	except Exception as e:
		frappe.log_error(str(e), "Shopee Invoice Patch Error")
		raise


def ensure_bank_transaction_from_escrow(escrow: Dict[str, Any]) -> str:
	"""Idempotently create Bank Transaction for Shopee payout."""
	import frappe
	order_sn = escrow.get("order_sn") or "UNKNOWN"
	payout_batch_id = escrow.get("payout_batch_id") or "BATCH-MOCK"
	net = float(escrow.get("net", 0))
	try:
		# Try to find existing Bank Transaction by reference and amount
		bt = frappe.get_doc({
			"doctype": "Bank Transaction",
			"reference_number": payout_batch_id,
			"amount": net
		})
		_log("bank_txn_exists", {"bt": bt.name, "order_sn": order_sn})
		return bt.name
	except frappe.DoesNotExistError:
		# Create new Bank Transaction (mock, real implementation should fill all required fields)
		bt = frappe.get_doc({
			"doctype": "Bank Transaction",
			"reference_number": payout_batch_id,
			"amount": net,
			"shopee_order_sn": order_sn
		})
		bt.insert(ignore_permissions=True)
		_log("bank_txn_created", {"bt": bt.name, "order_sn": order_sn})
		return bt.name
	except Exception as e:
		frappe.log_error(str(e), "Shopee Bank Transaction Error")
		raise


def sync_escrow_for_order(order_sn: str) -> Dict[str, Any]:
	"""Sync escrow + patch invoice + ensure bank transaction for one order.

	Returns summary with names (mock) and timing.
	"""
	started = time.time()
	escrow = get_escrow_detail(order_sn)
	if escrow.get("error"):
		return {"order_sn": order_sn, "error": escrow["error"]}
	invoice = patch_invoice_with_fees(escrow)
	bank_txn = ensure_bank_transaction_from_escrow(escrow)
	return {
		"order_sn": order_sn,
		"invoice": invoice,
		"bank_transaction": bank_txn,
		"duration_s": round(time.time() - started, 2),
	}


def sync_escrow_for_completed_orders(min_age_hours: int = 3, limit: int = 200) -> Dict[str, Any]:
	"""Pull recent completed orders & sync escrow (HIGH LEVEL STUB).

	Future data source: Sales Invoices / Sales Orders where status=Completed and
	escrow_synced != 1 and posting_date older than min_age_hours.
	For now, operates over a mock list.

	Args:
		min_age_hours: Minimum order completion age before escrow expected.
		limit: Max orders to process in one run.
	"""
	started = time.time()
	# TODO: query real orders. Using placeholder list.
	mock_orders = [f"MOCKORDER{i}" for i in range(1, min(limit, 5) + 1)]
	processed: List[Dict[str, Any]] = []
	errors: List[str] = []
	for sn in mock_orders:
		res = sync_escrow_for_order(sn)
		if res.get("error"):
			errors.append(f"{sn}: {res['error']}")
		else:
			processed.append(res)
	return {
		"count": len(processed),
		"errors": errors,
		"duration_s": round(time.time() - started, 2),
		"min_age_hours": min_age_hours,
		"limit": limit,
	}


def reconcile_bank_strict(days_back: int = 2) -> Dict[str, Any]:
	"""Attempt strict reconciliation of Shopee payouts (STUB).

	Planned algorithm:
	  - Collect unsettled bank transactions tagged as Shopee within date window.
	  - For each, attempt match to escrow-synced invoices by exact net amount and date.
	  - If single match, mark reconciled.
	  - If multiple matches or date mismatch, flag for manual review.
	  - Policy for date mismatch (TODO): adjust bank txn date or leave for manual? Document decision.

	Returns summary dict with counts (all mocked now).
	"""
	started = time.time()
	# Mock metrics
	metrics = {
		"transactions_considered": 0,
		"reconciled": 0,
		"ambiguous": 0,
		"needs_manual": 0,
		"days_back": days_back,
		"duration_s": round(time.time() - started, 2),
	}
	return metrics


def finance_backfill_range(start: str, end: str) -> Dict[str, Any]:
	"""Historical backfill over a date range (STUB).

	Args:
		start: ISO date (YYYY-MM-DD) inclusive.
		end: ISO date inclusive.

	Planned algorithm:
	  - Enumerate completed orders in date range (by completion date).
	  - For each, run `sync_escrow_for_order` if not already synced.
	  - Track counts & errors.
	"""
	started = time.time()
	# Mock iteration placeholder
	summary = {
		"start": start,
		"end": end,
		"orders": 0,
		"synced": 0,
		"errors": [],
		"duration_s": round(time.time() - started, 2),
	}
	return summary


def log_escrow(site: str, order_sn: str, payload: dict) -> str:
	import frappe
	from shopee_bridge import helpers
	existing = frappe.get_all("Shopee Sync Log",
							  filters={"category": "escrow", "ref": order_sn},
							  pluck="name", limit=1)
	if existing:
		doc = frappe.get_doc("Shopee Sync Log", existing[0])
		doc.payload_json = frappe.as_json(payload)
		doc.status = "DONE"
		doc.created_epoch = helpers.now_epoch()
		doc.save(ignore_permissions=True)
		return doc.name
	doc = frappe.get_doc({
		"doctype": "Shopee Sync Log",
		"category": "escrow",
		"ref": order_sn,
		"payload_json": frappe.as_json(payload),
		"status": "DONE",
		"created_epoch": helpers.now_epoch(),
	})
	doc.insert(ignore_permissions=True)
	return doc.name


__all__ = [
	"get_escrow_detail",
	"patch_invoice_with_fees",
	"ensure_bank_transaction_from_escrow",
	"sync_escrow_for_order",
	"sync_escrow_for_completed_orders",
	"reconcile_bank_strict",
	"finance_backfill_range",
]

