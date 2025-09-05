"""Incremental returns sync job (stub orchestrator)."""

from typing import Dict, Any
import time
import frappe


def run(minutes: int = 30) -> Dict[str, Any]:
    now_ts = int(time.time())
    from_ts = now_ts - minutes * 60
    summary: Dict[str, Any] = {
        "minutes": minutes,
        "window_from": from_ts,
        "window_to": now_ts,
        "returns_found": 0,
        "processed": 0,
        "errors": [],
    }
    from ..services import returns as returns_service
    from ..shopee_bridge.doctype.shopee_sync_log.shopee_sync_log import ShopeeSyncLog
    try:
        # Use service incremental stub
        svc = returns_service.sync_returns_incremental(updated_since_minutes=minutes)
        summary.update({
            "returns_found": svc.get("returns_found", 0),
            "processed": svc.get("returns_processed", 0),
            "errors": svc.get("errors", []),
        })
        status = "ok" if not summary["errors"] else "partial"
        ShopeeSyncLog.write_log("sync_returns", f"window:{from_ts}-{now_ts}", status, meta=summary)
    except Exception as exc:  # pragma: no cover
        msg = str(exc)
        summary["errors"].append(msg)
        ShopeeSyncLog.write_log("sync_returns", f"window:{from_ts}-{now_ts}", "fail", message=msg)
    return summary

