"""Incremental order sync job.

Performs a lightweight pull of order list + details for a recent window and
invokes ensure* stubs for Sales Order / Invoice / Delivery Note creation.

Design:
 - Imports service modules lazily to avoid circulars.
 - Catches per-order exceptions and continues.
 - Writes aggregated Shopee Sync Log entry and per-order error logs.
 - Returns summary dict (JSON friendly) with counters.
"""

from typing import Dict, Any, List
import time
import frappe


def run(minutes: int = 10) -> Dict[str, Any]:
    now_ts = int(time.time())
    window_from = now_ts - minutes * 60
    summary: Dict[str, Any] = {
        "minutes": minutes,
        "window_from": window_from,
        "window_to": now_ts,
        "orders_found": 0,
        "processed": 0,
        "errors": [],
    }
    from ..services import orders  # local import
    from ..shopee_bridge.doctype.shopee_sync_log.shopee_sync_log import ShopeeSyncLog
    
    try:
        sns: List[str] = orders.get_order_list(window_from, now_ts, status=None)
        summary["orders_found"] = len(sns)
        details = orders.get_order_detail(sns)
        for od in details:
            order_sn = od.get("order_sn") or "UNKNOWN"
            try:
                so = orders.upsert_sales_order(od)
                status = (od.get("order_status") or "").lower()
                si = None
                dn = None
                if status in {"paid", "ready_to_ship", "completed"}:
                    si = orders.ensure_sales_invoice_for_paid(so, od)
                if status in {"ready_to_ship", "completed"}:
                    dn = orders.ensure_delivery_note_for_ready(si or so, od)
                if status == "completed":
                    orders.on_completed(order_sn)
                summary["processed"] += 1
            except Exception as per_exc:  # pragma: no cover
                msg = f"{order_sn}: {per_exc}"[:400]
                summary["errors"].append(msg)
                ShopeeSyncLog.write_log("sync_orders", order_sn, "fail", message=msg)
        status = "ok" if not summary["errors"] else "partial"
        ShopeeSyncLog.write_log("sync_orders", f"window:{window_from}-{now_ts}", status, meta=summary)
    except Exception as exc:  # fatal
        summary["errors"].append(str(exc))
        ShopeeSyncLog.write_log("sync_orders", f"window:{window_from}-{now_ts}", "fail", message=str(exc))
    return summary

