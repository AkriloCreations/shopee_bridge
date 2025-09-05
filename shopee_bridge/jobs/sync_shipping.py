"""Incremental shipping (logistics) sync job (stub)."""
import frappe
from typing import Dict, Any
import time



def run(minutes: int = 30) -> Dict[str, Any]:
    now_ts = int(time.time())
    from_ts = now_ts - minutes * 60
    summary: Dict[str, Any] = {
        "minutes": minutes,
        "window_from": from_ts,
        "window_to": now_ts,
        "updates_found": 0,
        "processed": 0,
        "errors": [],
    }
    from ..services import logistics
    from ..shopee_bridge.doctype.shopee_sync_log.shopee_sync_log import ShopeeSyncLog
    try:
        svc = logistics.sync_shipping_status(updated_since_minutes=minutes)
        summary.update({
            "updates_found": svc.get("updates_found", 0),
            "processed": svc.get("updates_processed", 0),
            "errors": svc.get("errors", []),
        })
        status = "ok" if not summary["errors"] else "partial"
        ShopeeSyncLog.write_log("sync_shipping", f"window:{from_ts}-{now_ts}", status, meta=summary)
    except Exception as exc:  # pragma: no cover
        msg = str(exc)
        summary["errors"].append(msg)
        ShopeeSyncLog.write_log("sync_shipping", f"window:{from_ts}-{now_ts}", "fail", message=msg)
    return summary

